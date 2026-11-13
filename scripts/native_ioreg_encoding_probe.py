"""Reversible output-register control encoding on isolated physical objects."""
import ctypes as C
import gzip,json,shutil,struct
import numpy as np
from ownpr_device import Device
from ownpr_lut_configuration import NativeFrameProjection
from ownpr_ioreg_configuration import IoregChoices,output_attributes
from audit_native_encoding_probe import payload
from ownpr_place import sha


def probe(native,out,objects,cell_type='DFFCE',combined_controls=False,site_override=None):
    if cell_type not in ('DFFCE','DFFRE'):raise ValueError('Unsupported encoding probe primitive')
    ptr=native.ptr;fn=native.fn;vp=C.c_void_p;device=Device();projection=NativeFrameProjection(device);choices=IoregChoices(device);site=site_override or [110,135,0]
    block=fn('?get_blk@iob@NETLIST@@QEAAPEAVioblk@2@F@Z',vp,[vp,C.c_short])(native.getgrid(native.chip,*site[:2]),site[2]);io=ptr(block+0x28);reg=ptr(io+0x818);bufphy=ptr(ptr(block+0x10))
    obj=objects[cell_type.lower()];output=objects['ob2' if cell_type=='DFFCE' else 'ob0'];setphy=fn('?setPhyInst@Instance@NETLIST@@QEAAXPEAVinstance@2@@Z',None,[vp,vp])
    setphy(obj,reg);C.c_uint64.from_address(reg+8).value=obj;fn('?setPin2Node@Reg@NETLIST@@UEAAXXZ',None,[vp])(obj)
    setphy(output,bufphy);C.c_uint64.from_address(bufphy+8).value=output;fn('?setIobufLogic@iobuf@NETLIST@@QEAAXPEAVIobuf@2@@Z',None,[vp,vp])(bufphy,output);fn('?setPin2Node@Iobuf@NETLIST@@UEAAXXZ',None,[vp])(output)
    pin=fn('?getOnePin@Reg@NETLIST@@UEAAPEAVPin@2@I@Z',vp,[vp,C.c_uint]);pins={k:pin(obj,i) for k,i in [('ce',2),('control',6 if cell_type=='DFFCE' else 4),('clock',1)]}
    original={k:(ptr(p+8),C.c_ubyte.from_address(p+0x4f).value) for k,p in pins.items()};sources={'one':ptr(pin(objects['sentinel'],2)+8),'zero':ptr(pin(objects['sentinel'],4)+8)}
    encode=fn('?generateFusemap@@YAHPEAVglobalMem@@@Z',C.c_int,[vp]);fs=out/'probe.fs'
    def apply(profile):
        C.memset(io+0x840,0,342)
        for k,p in pins.items():
            label=profile[k];C.c_uint64.from_address(p+8).value=original[k][0] if label in ('signal','inverted') else sources[label]
            C.c_ubyte.from_address(p+0x4f).value=(original[k][1]&~0x40)|(0x40 if label=='inverted' else 0)
    def emit(profile):
        apply(profile)
        if encode(native.mem):raise ValueError('Isolated output-register encoding failed')
        expected=output_attributes(cell_type,**profile);actual=list(struct.unpack('<171h',C.string_at(io+0x840,342)))
        if actual!=expected:raise ValueError('Encoded output-register attributes differ from independent model')
        required=(choices.observed_requirements if site_override else choices.requirements)(site,expected);image=projection.read(fs)
        if any(image[projection.index(bit)]-48!=value for bit,value in required.items()):raise ValueError('Output-register configuration bits differ from independent model')
        return image,required
    def save(label):
        path=out/(label+'.fs.gz')
        with fs.open('rb') as src,gzip.open(path,'wb',compresslevel=1) as dst:shutil.copyfileobj(src,dst)
        return path.name
    base=dict(ce='signal',control='signal',clock='signal')
    for _ in range(2):emit(base)
    stable=payload(fs);baseline,base_req=emit(base)
    if payload(fs)!=stable:raise ValueError('Output-register calibration baseline did not stabilize')
    baseline_file=save('ioreg_baseline');records=[]
    for field,labels in [('ce',('zero','one','inverted')),('control',('zero','one','inverted')),('clock',('inverted',))]:
        for label in labels:
            profile=dict(base);profile[field]=label;data,required=emit(profile);variant=save('ioreg_'+field+'_'+label)
            actual=set(np.flatnonzero(data!=baseline).tolist());predicted={projection.index(bit) for bit,value in required.items() if value!=base_req[bit]}
            if actual!=predicted:raise ValueError('Unexplained output-register encoding differences')
            emit(base);restored=payload(fs)==stable
            if not restored:raise ValueError('Output-register encoding did not restore full payload')
            records.append(dict(profile=profile,variant=variant,variant_sha256=sha(out/variant),changed_bits=len(actual),expected_bits=len(predicted),
                changes=[dict(index=i,before=int(baseline[i])-48,after=int(data[i])-48) for i in sorted(actual)],full_payload_restored=restored))
            print('IOREG_ENCODING',field,label,len(actual),flush=True)
    restored_file=save('ioreg_restored')
    if combined_controls:
        profile=dict(ce='one',control='zero',clock='signal');data,required=emit(profile);variant=save('ioreg_constant_controls')
        actual=set(np.flatnonzero(data!=baseline).tolist());predicted={projection.index(bit) for bit,value in required.items() if value!=base_req[bit]}
        if actual!=predicted:raise ValueError('Combined constant-control encoding differs')
        emit(base)
        if payload(fs)!=stable:raise ValueError('Combined controls did not restore full payload')
        records.append(dict(profile=profile,variant=variant,variant_sha256=sha(out/variant),changed_bits=len(actual),expected_bits=len(predicted),full_payload_restored=True))
    result=dict(status='isolated_output_ioreg_encoding_matched',site=site,observed_site_override=site_override is not None,cell_type=cell_type,combined_constant_controls_checked=combined_controls,baseline_profile=base,baseline=baseline_file,baseline_sha256=sha(out/baseline_file),
        restored=restored_file,restored_sha256=sha(out/restored_file),baseline_required_bits=len(base_req),records=records,source_fse_sha256=device.fse.sha256,
        vendor_pnr_executed=False,full_bitstream_verified=False,scope='Reversible output-register control perturbations; no clock/data routing or usable image claim.')
    (out/'ioreg_encoding_probe.json').write_text(json.dumps(result,indent=2))
