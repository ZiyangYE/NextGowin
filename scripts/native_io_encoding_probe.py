"""Reversible I/O and GSR encoding experiments on imported own P&R state."""
import ctypes as C
import gzip,json,shutil
import numpy as np
from native_io_state import capture
from ownpr_device import Device
from ownpr_lut_configuration import NativeFrameProjection
from audit_native_encoding_probe import payload


def probe(native,out,objects,mapping):
    fn=native.fn;vp=C.c_void_p;ptr=native.ptr;fs=out/'probe.fs';projection=NativeFrameProjection(Device())
    state=capture(native)
    if state['attributes']['2']!='PULL_MODE' or state['attributes']['3']!='DRIVE' or state['values']['116']!='UP' or state['values']['96']!='4':raise ValueError('Unreviewed I/O enums')
    encode=fn('?generateFusemap@@YAHPEAVglobalMem@@@Z',C.c_int,[vp])
    setpio=fn('?setPioAttribute@Iobuf@NETLIST@@QEAAHW4PIO_ATTR@2@W4PIO_VALUE@2@@Z',C.c_int,[vp,C.c_int,C.c_int])
    getphy=fn('?getPhyInst@Instance@NETLIST@@QEAAPEAVinstance@2@XZ',vp,[vp])
    setphysical=fn('?setPioAttr@iobuf@NETLIST@@QEAAXW4PIO_ATTR@2@W4PIO_VALUE@2@@Z',None,[vp,C.c_int,C.c_int])
    # Save the original baseline before calibration. Every test restores it.
    def save(name):
        path=out/(name+'.fs.gz')
        with fs.open('rb') as src,gzip.open(path,'wb') as dst:shutil.copyfileobj(src,dst)
        return path.name
    production_file=save('production_single_pass');production=projection.read(fs)
    if encode(native.mem):raise ValueError('Calibration warmup failed')
    baseline=projection.read(fs);baseline_payload=payload(fs)
    baseline_file=save('configuration_baseline');records=[]
    def evaluate(label,change,restore,info):
        change()
        try:
            if encode(native.mem):raise ValueError('Calibration encoding failed')
            data=projection.read(fs);diff=np.flatnonzero(data!=baseline);file=save(label)
            record=dict(label=label,variant=file,changes=[dict(index=int(i),before=int(baseline[i])-48,after=int(data[i])-48) for i in diff],**info)
        finally:
            restore()
            if encode(native.mem):raise ValueError('Restored encoding failed')
        if payload(fs)!=baseline_payload:
            (out/'restore_failure_state.json').write_text(json.dumps(capture(native),indent=2))
            (out/'failed_variant.json').write_text(json.dumps(record,indent=2))
            raise ValueError('Complete configuration payload did not restore: '+label)
        record.update(changed_bits=len(diff),full_payload_restored=True);records.append(record)
        (out/'configuration_probe.json').write_text(json.dumps(dict(status='in_progress',baseline=baseline_file,records=records),indent=2))
        print('CONFIG_PROBE',label,len(diff),flush=True)
    for row in state['io']:
        obj=objects[row['instance']]
        variants=[('pull_up',2,116)]
        if row['logical'][3]==98:variants.append(('drive_4',3,96))
        for tag,attr,value in variants:
            old=row['logical'][attr]
            def change(obj=obj,attr=attr,value=value):
                if setpio(obj,attr,value):raise ValueError('I/O attribute change rejected')
                setphysical(getphy(obj),attr,value)
            def restore(obj=obj,attr=attr,old=old):
                if setpio(obj,attr,old):raise ValueError('I/O attribute restore rejected')
                setphysical(getphy(obj),attr,old)
            evaluate(row['instance']+'_'+tag,change,restore,dict(kind='io',instance=row['instance'],site=row['site'],attribute=attr,original=old,variant_value=value))
    design=fn('?getDesign@globalMem@@QEAAPEAVDesign@NETLIST@@XZ',vp,[vp])(native.mem)
    obj=fn('?getGsr@Design@NETLIST@@QEAAPEAVGsr@2@XZ',vp,[vp])(design);phy=fn('?getGsr@chip@NETLIST@@QEAAPEAVgsr@2@XZ',vp,[vp])(native.chip)
    setphy=fn('?setPhyInst@Instance@NETLIST@@QEAAXPEAVinstance@2@@Z',None,[vp,vp]);setlogic=fn('?setLogicInst@instance@NETLIST@@QEAAXPEAVInstance@2@@Z',None,[vp,vp])
    cached=ptr(phy+0x20)
    def detach():
        setphy(obj,None);setlogic(phy,None);C.c_uint64.from_address(phy+0x20).value=0
    def restore_gsr():
        setphy(obj,phy);setlogic(phy,obj);C.c_uint64.from_address(phy+0x20).value=cached
    evaluate('gsr_unbound',detach,restore_gsr,dict(kind='gsr',diagnostic_only=True))
    save('configuration_restored')
    default_indices=np.flatnonzero(production!=baseline)
    result=dict(status='restored',baseline=baseline_file,production=production_file,restored='configuration_restored.fs.gz',records=records,io_cases=sum(r['kind']=='io' for r in records),
        calibration_default_differences=[dict(index=int(i),production=int(production[i])-48,calibration=int(baseline[i])-48) for i in default_indices],
        full_payload_restored=True,vendor_pnr_executed=False,device_programmed=False,full_bitstream_verified=False)
    (out/'configuration_probe.json').write_text(json.dumps(result,indent=2))
    return result
