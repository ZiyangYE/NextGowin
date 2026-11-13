"""Reversible isolated LUT5/6/7 mux encoding with already-bound leaf LUTs."""
import argparse,gzip,json,os,shutil,struct,subprocess,sys,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from gowin_probe import prepare
from run_abc_reproduction import sha
from capture_runtime_tables import EXPECTED
from native_mux_inventory import fixture


def tree_slots(level,slot=None,index=0):
    slot={5:0,6:1,7:3}[level] if slot is None else slot
    muxes=[(f'mux_{level}_{index}',slot)];leaves=[]
    if level==5:leaves=[(f'lut_{2*index}',slot),(f'lut_{2*index+1}',slot+1)]
    else:
        step=1<<(level-6)
        for child,s in ((2*index,slot+step),(2*index+1,slot-step)):
            m,l=tree_slots(level-1,s,child);muxes+=m;leaves+=l
    return muxes,leaves


def worker(out):
    import ctypes as C
    import numpy as np
    from native_device_session import NativeDeviceSession
    from ownpr_device import Device
    from ownpr_lut_configuration import NativeFrameProjection
    from audit_native_encoding_probe import payload
    n=NativeDeviceSession(out);ptr=n.ptr;fn=n.fn;vp=C.c_void_p;d=Device();projection=NativeFrameProjection(d)
    design=fn('?getDesign@globalMem@@QEAAPEAVDesign@NETLIST@@XZ',vp,[vp])(n.mem);vec=(C.c_uint64*3)()
    fn('?getHierAllInsts@Design@NETLIST@@QEAAXAEAV?$vector@PEAVInstance@NETLIST@@V?$allocator@PEAVInstance@NETLIST@@@std@@@std@@@Z',None,[vp,vp])(design,C.addressof(vec))
    getname=fn('?getName@Instance@NETLIST@@QEAA?AV?$basic_string@DU?$char_traits@D@std@@V?$allocator@D@2@@std@@XZ',vp,[vp,vp]);objects={}
    for a in range(vec[0],vec[1],8):
        obj=ptr(a);buf=C.create_string_buffer(32);getname(obj,C.addressof(buf));p=C.addressof(buf);length=ptr(p+16)
        if length>4096:raise ValueError('Invalid native object name')
        objects[C.string_at(ptr(p) if ptr(p+24)>=16 else p,length).decode()]=obj
    setphy=fn('?setPhyInst@Instance@NETLIST@@QEAAXPEAVinstance@2@@Z',None,[vp,vp]);setlogic=fn('?setLogicInst@instance@NETLIST@@QEAAXPEAVInstance@2@@Z',None,[vp,vp])
    bindlut=fn('?setPin2Node@Lut4@NETLIST@@UEAAXXZ',None,[vp]);bindmux=fn('?setPin2Node@Mux2@NETLIST@@UEAAXXZ',None,[vp])
    setinit=fn('?setInitValueForRouted@Lut4@NETLIST@@QEAAXV?$basic_string@DU?$char_traits@D@std@@V?$allocator@D@2@@std@@@Z',None,[vp,vp]);encode=fn('?generateFusemap@@YAHPEAVglobalMem@@@Z',C.c_int,[vp])
    aggregate=ptr(n.getgrid(n.chip,50,90)+0x70);groups=[ptr(aggregate+0x360+8*i) for i in range(4)];fs=out/'probe.fs'
    def attrs():return [list(struct.unpack('<19h',C.string_at(p+6,38))) for p in groups]
    def snapshot():return (C.string_at(aggregate+0x318,0x48),[C.string_at(p+6,38) for p in groups],[C.string_at(ptr(aggregate+8*i)+0x20,3) for i in range(8)])
    def restore(saved):
        flags,clss,luts=saved;C.memmove(aggregate+0x318,flags,len(flags))
        for p,raw in zip(groups,clss):C.memmove(p+6,raw,len(raw))
        for i,raw in enumerate(luts):C.memmove(ptr(aggregate+8*i)+0x20,raw,len(raw))
    def save(label):
        path=out/(label+'.fs.gz')
        with fs.open('rb') as src,gzip.open(path,'wb') as dst:shutil.copyfileobj(src,dst)
        return path.name
    pristine=snapshot();records=[]
    for level in (5,6,7):
        muxes,leaves=tree_slots(level);leaf_words=[]
        for name,slot in leaves:
            obj=objects[name];phy=ptr(aggregate+slot*8);setphy(obj,phy);setlogic(phy,obj);bindlut(obj)
            word=(0xA639^(int(name.split('_')[-1])*0x131))&0xffff;text=f"16'h{word:04X}".encode();arg=C.create_string_buffer(32);C.memmove(arg,text,len(text));C.c_uint64.from_address(C.addressof(arg)+16).value=len(text);C.c_uint64.from_address(C.addressof(arg)+24).value=15;setinit(obj,C.addressof(arg))
            C.c_ushort.from_address(phy+0x20).value=word;C.c_ubyte.from_address(phy+0x22).value=0;C.c_ubyte.from_address(aggregate+0x326+slot).value=1;leaf_words.append(dict(instance=name,slot=slot,word=word))
        for _ in range(2):
            if encode(n.mem):raise ValueError('Unactivated leaf encoding failed')
        inactive_payload=payload(fs)
        if encode(n.mem) or payload(fs)!=inactive_payload:raise ValueError('Inactive calibration profile did not stabilize')
        inactive=projection.read(fs);inactive_file=save(f'lut{level}_inactive_leaf')
        # Match the completed native LUT-only CLS profile. Leaving primary
        # modes at zero hides three default FF-control bits per occupied CLS.
        for group in {slot//2 for _,slot in leaves}:C.c_short.from_address(groups[group]+6).value=9
        for _ in range(2):
            if encode(n.mem):raise ValueError('Leaf baseline encoding failed')
        baseline=projection.read(fs);base_payload=payload(fs);base_file=save(f'lut{level}_leaf_baseline');baseline_attrs=attrs();saved=snapshot()
        for name,slot in reversed(muxes):
            obj=objects[name];phy=ptr(aggregate+0x150+8*slot);setphy(obj,phy);setlogic(phy,obj);C.c_uint64.from_address(phy+0x18).value=obj;bindmux(obj)
        if encode(n.mem):raise ValueError('Dedicated mux encoding failed')
        variant=projection.read(fs);different=np.flatnonzero(baseline!=variant);variant_file=save(f'lut{level}_mux_bound');after_attrs=attrs()
        record=dict(level=level,site=[50,90],tile_type=d.grid[49][89],muxes=[dict(instance=name,slot=slot) for name,slot in muxes],leaf_words=leaf_words,
            inactive_leaf=inactive_file,cls_activation_changes=[dict(index=int(i),before=int(inactive[i])-48,after=int(baseline[i])-48) for i in np.flatnonzero(inactive!=baseline)],
            baseline=base_file,variant=variant_file,baseline_cls=baseline_attrs,encoded_cls=after_attrs,changed_bits=len(different),
            changes=[dict(index=int(i),before=int(baseline[i])-48,after=int(variant[i])-48) for i in different])
        for name,slot in muxes:
            phy=ptr(aggregate+0x150+8*slot);setphy(objects[name],None);fn('?setPhyMux2@Mux2@NETLIST@@QEAAXPEAVmux2@2@@Z',None,[vp,vp])(objects[name],None);setlogic(phy,None);C.c_uint64.from_address(phy+0x18).value=0
        restore(saved)
        if encode(n.mem):raise ValueError('Dedicated mux restore failed')
        record['restored']=save(f'lut{level}_restored');record['full_payload_restored']=payload(fs)==base_payload;records.append(record)
        (out/'mux_encoding_probe.json').write_text(json.dumps(dict(records=records,scope='Mux binding differential over already-bound LUT leaves; normal PIPs, clock/I/O and complete image validity are separate'),indent=2));print('MUX_ENCODING',level,len(different),record['full_payload_restored'],flush=True)
        if not record['full_payload_restored']:raise ValueError('Mux encoding failed full-payload restoration')
        for name,slot in leaves:
            setphy(objects[name],None);fn('?setPhyLut4@Lut4@NETLIST@@QEAAXPEAVlut4@2@@Z',None,[vp,vp])(objects[name],None);setlogic(ptr(aggregate+8*slot),None)
        restore(pristine)


def main():
    p=argparse.ArgumentParser();p.add_argument('--out',type=Path,required=True);p.add_argument('--worker',action='store_true');a=p.parse_args();out=a.out.resolve()
    if a.worker:return worker(out)
    prepare(out);(out/'probe.vg').write_text(fixture());cmd=out/'cmd.do';cmd.write_text(cmd.read_text()+'-bit\n');shutil.copyfile(__file__,out/'capture_source.py.txt')
    python=ROOT/'binary_audit/flow/runtime/bin/python.exe';start=time.monotonic();rc=None;expired=False
    with (out/'worker.log').open('w') as log:
        try:rc=subprocess.run([str(python),str(Path(__file__).resolve()),'--worker','--out',str(out)],cwd=python.parent,env=dict(os.environ,PYTHONHOME=sys.base_prefix),stdout=log,stderr=subprocess.STDOUT,timeout=240).returncode
        except subprocess.TimeoutExpired:expired=True
    r=dict(status='captured' if rc==0 and (out/'mux_encoding_probe.json').exists() else 'failed',returncode=rc,timeout=expired,wall_seconds=time.monotonic()-start,dll_sha256=EXPECTED,vendor_pnr_executed=False,open_chip_database_used=False)
    if (out/'mux_encoding_probe.json').exists():r['capture_sha256']=sha(out/'mux_encoding_probe.json')
    (out/'manifest.json').write_text(json.dumps(r,indent=2));print(json.dumps(r,indent=2))
    if r['status']!='captured':raise SystemExit(1)


if __name__=='__main__':main()
