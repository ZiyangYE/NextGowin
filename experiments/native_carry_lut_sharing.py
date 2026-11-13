"""Isolated native evidence for ALU[0:5] with ordinary LUT[6:7].

No vendor packing/placement/routing is executed. This tests binding and fuse
composition; real route connectivity and whole-design STA remain separate.
"""
import argparse,json,os,shutil,subprocess,sys,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from ownpr_image_storage import sha

def worker(out):
    from native_device_session import NativeDeviceSession
    n=NativeDeviceSession(out)
    import ctypes as C,struct,gzip
    import numpy as np
    from ownpr_device import Device,DLL_SHA
    from ownpr_lut_configuration import NativeFrameProjection,LutInitChoices
    from ownpr_alu_configuration import Mode2Configuration
    from ownpr_ff_configuration import independent_ff_groups
    from ownpr_cls_configuration import ClsChoices
    from audit_native_encoding_probe import payload
    dev=Device();projection=NativeFrameProjection(dev);codec=LutInitChoices(dev);alu=Mode2Configuration(dev);cls=ClsChoices(dev)
    vp=C.c_void_p;ptr=n.ptr;fn=n.fn;objects={};design=fn('?getDesign@globalMem@@QEAAPEAVDesign@NETLIST@@XZ',vp,[vp])(n.mem);vec=(C.c_uint64*3)()
    fn('?getHierAllInsts@Design@NETLIST@@QEAAXAEAV?$vector@PEAVInstance@NETLIST@@V?$allocator@PEAVInstance@NETLIST@@@std@@@std@@@Z',None,[vp,vp])(design,C.addressof(vec))
    getname=fn('?getName@Instance@NETLIST@@QEAA?AV?$basic_string@DU?$char_traits@D@std@@V?$allocator@D@2@@std@@XZ',vp,[vp,vp])
    for address in range(vec[0],vec[1],8):
        obj=ptr(address);buf=C.create_string_buffer(32);getname(obj,C.addressof(buf));p=C.addressof(buf);length=ptr(p+16)
        if length>64:raise ValueError('Invalid fixture name')
        objects[C.string_at(ptr(p) if ptr(p+24)>=16 else p,length).decode()]=obj
    setphy=fn('?setPhyInst@Instance@NETLIST@@QEAAXPEAVinstance@2@@Z',None,[vp,vp]);setlogic=fn('?setLogicInst@instance@NETLIST@@QEAAXPEAVInstance@2@@Z',None,[vp,vp])
    bind={k:fn(f'?setPin2Node@{k}@NETLIST@@UEAAXXZ',None,[vp]) for k in ('Alu','Lut4')}
    getpin={k:fn(f'?getOnePin@{k}@NETLIST@@UEAAPEAVPin@2@I@Z',vp,[vp,C.c_uint]) for k in ('Alu','Lut4')}
    setinit=fn('?setInitValueForRouted@Lut4@NETLIST@@QEAAXV?$basic_string@DU?$char_traits@D@std@@V?$allocator@D@2@@std@@@Z',None,[vp,vp]);encode=fn('?generateFusemap@@YAHPEAVglobalMem@@@Z',C.c_int,[vp])
    names=[f'a{i}' for i in range(6)]+['l6','l7']
    if not set(names)<=set(objects):raise ValueError('Fixture instances missing')
    for name in names:setphy(objects[name],None)
    fs=out/'probe.fs'
    for _ in range(2):
        if encode(n.mem):raise ValueError('Baseline encoding failed')
    baseline=projection.read(fs);base_payload=payload(fs)
    def save(label):
        target=out/(label+'.fs.gz')
        with fs.open('rb') as src,gzip.open(target,'wb') as dst:shutil.copyfileobj(src,dst)
        return target.name
    baseline_name=save('baseline');sites=[(50,90)]
    sites.append(next((r,c) for r in range(51,55) for c in range(90,95) if dev.grid[r-1][c-1]==17));records=[]
    for r,c in sites:
        if dev.grid[r-1][c-1]!=17:raise ValueError('Uncalibrated fixture tile')
        agg=ptr(n.getgrid(n.chip,r,c)+0x70);groups=[ptr(agg+0x360+8*i) for i in range(4)]
        saved_groups=[C.string_at(p+6,38) for p in groups];saved_words=[C.string_at(ptr(agg+8*i)+0x20,3) for i in range(8)];saved_flags=C.string_at(agg+0x318,0x48)
        def configure(use_alu,use_lut):
            base_attrs=independent_ff_groups([])
            attrs=alu.with_alu_modes(base_attrs,range(6)) if use_alu else base_attrs
            for group,values in zip(groups,attrs):C.memmove(group+6,struct.pack('<19h',*values),38)
            bindings=[];requirements={}
            for i,values in enumerate(attrs):requirements.update(cls.requirements(r-1,c-1,i,values))
            if use_alu:
                for slot in range(6):
                    obj=objects[f'a{slot}'];phy=ptr(agg+0x40+8*slot);setphy(obj,phy);setlogic(phy,obj);C.c_uint64.from_address(phy+0x20).value=obj;bind['Alu'](obj)
                    requirements.update(alu.requirements(r-1,c-1,slot,2,['signal']*4,attrs[slot//2]));bindings.append((f'a{slot}','Alu',6))
            if use_lut:
                for slot,word in ((6,0x6996),(7,0x96e8)):
                    obj=objects[f'l{slot}'];phy=ptr(agg+8*slot);setphy(obj,phy);setlogic(phy,obj)
                    text=f"16'h{word:04X}".encode();arg=C.create_string_buffer(32);C.memmove(arg,text,len(text));C.c_uint64.from_address(C.addressof(arg)+16).value=len(text);C.c_uint64.from_address(C.addressof(arg)+24).value=15;setinit(obj,C.addressof(arg))
                    C.c_ushort.from_address(phy+0x20).value=word;C.c_ubyte.from_address(phy+0x22).value=0;C.c_ubyte.from_address(agg+0x326+slot).value=1;bind['Lut4'](obj)
                    req=codec.requirements(r-1,c-1,slot,word)
                    if any(b in requirements and requirements[b]!=v for b,v in req.items()):raise ValueError('Predicted fuse collision')
                    requirements.update(req);bindings.append((f'l{slot}','Lut4',5))
            pins={}
            for name,kind,count in bindings:
                pins[name]=[]
                for direction in ('In','Out'):
                    vector=(C.c_uint64*3)()
                    fn(f'?getAll{direction}Pins@{kind}@NETLIST@@UEAA?AV?$vector@PEAVPin@NETLIST@@V?$allocator@PEAVPin@NETLIST@@@std@@@std@@XZ',vp,[vp,vp])(objects[name],C.addressof(vector))
                    for address in range(vector[0],vector[1],8):
                        node=ptr(ptr(address)+0x18)
                        pins[name].append(dict(direction=direction,node=list(struct.unpack('<3h',C.string_at(node,6))) if node else None))
                if len(pins[name])!=count or any(x['node'] is None for x in pins[name]):raise ValueError('Incomplete fixture pin binding')
            return requirements,pins
        def restore():
            for slot in range(6):
                phy=ptr(agg+0x40+8*slot);setphy(objects[f'a{slot}'],None);setlogic(phy,None);C.c_uint64.from_address(phy+0x20).value=0
            for slot in (6,7):setphy(objects[f'l{slot}'],None);setlogic(ptr(agg+8*slot),None)
            for group,raw in zip(groups,saved_groups):C.memmove(group+6,raw,38)
            for i,raw in enumerate(saved_words):C.memmove(ptr(agg+8*i)+0x20,raw,3)
            C.memmove(agg+0x318,saved_flags,len(saved_flags))
        for label,use_alu,use_lut in (('alu',True,False),('lut',False,True),('mixed',True,True)):
            req,pins=configure(use_alu,use_lut)
            if encode(n.mem):raise ValueError('Fixture encode failed')
            image=projection.read(fs);bad=[list(b) for b,v in req.items() if int(image[projection.index(b)])-48!=v]
            record=dict(site=[r,c],tile_type=17,label=label,variant=save(f'{r}_{c}_{label}'),pins=pins,requirements=len(req),mismatches=bad,changed_bits=int(np.count_nonzero(image!=baseline)),
                cls_attributes=[list(struct.unpack('<19h',C.string_at(g+6,38))) for g in groups])
            restore()
            if encode(n.mem):raise ValueError('Restore encode failed')
            record['full_payload_restored']=payload(fs)==base_payload;records.append(record)
            (out/'capture.json').write_text(json.dumps(dict(baseline=baseline_name,records=records),indent=2));print('CASE',r,c,label,len(bad),record['full_payload_restored'],flush=True)
            if bad or not record['full_payload_restored']:raise ValueError('Mixed-mode calibration failed')
    for site in sites:
        rows={x['label']:x for x in records if x['site']==list(site)}
        if {**rows['alu']['pins'],**rows['lut']['pins']}!=rows['mixed']['pins']:raise ValueError('Mixed mode changed pin binding')
        av=projection.read(out/rows['alu']['variant']);lv=projection.read(out/rows['lut']['variant']);mv=projection.read(out/rows['mixed']['variant'])
        if not np.array_equal(mv,np.where(av!=baseline,av,lv)):raise ValueError('Unexpected mixed-mode bitmap interaction')
        outputs=[tuple(v['node']) for pins in rows['mixed']['pins'].values() for v in pins if v['direction']=='Out']
        if len(outputs)!=len(set(outputs)):raise ValueError('ALU and LUT physical outputs overlap')
    (out/'audit.json').write_text(json.dumps(dict(status='mixed_carry_lut_encoding_and_binding_verified',sites=sites,tile_type=17,alu_slots=list(range(6)),lut_slots=[6,7],ff_sharing_supported=False,requirements_match=True,pin_bindings_unchanged=True,ordinary_bitmap_composition_match=True,full_payload_restored=True,dll_sha256=DLL_SHA,fse_sha256=dev.fse.sha256,capture_sha256=sha(out/'capture.json'),full_route_sta_verified=False,hardware_verified=False),indent=2))

def main():
    p=argparse.ArgumentParser();p.add_argument('--out',type=Path,required=True);p.add_argument('--worker',action='store_true');a=p.parse_args();out=a.out.resolve()
    if a.worker:return worker(out)
    from workspace_budget import reserve
    from gowin_probe import prepare
    from ownpr_device import DLL_SHA
    reserve(ROOT,900_000_000);prepare(out)
    lines=['module top(a,q);input [3:0] a;output [13:0] q;wire [3:0] x;wire [13:0] y;']
    lines += [f'IBUF ib{i}(.I(a[{i}]),.O(x[{i}]));' for i in range(4)]
    for i in range(6):lines += [f'ALU a{i}(.I0(x[0]),.I1(x[1]),.I3(x[2]),.CIN(x[3]),.SUM(y[{i*2}]),.COUT(y[{i*2+1}]));defparam a{i}.ALU_MODE=2;']
    for i,word in ((6,'6996'),(7,'96E8')):lines += [f"LUT4 l{i}(.I0(x[0]),.I1(x[1]),.I2(x[2]),.I3(x[3]),.F(y[{i+6}]));defparam l{i}.INIT=16'h{word};"]
    lines += [f'OBUF ob{i}(.I(y[{i}]),.O(q[{i}]));' for i in range(14)]+['endmodule']
    (out/'probe.vg').write_text('\n'.join(lines));cmd=out/'cmd.do';cmd.write_text(cmd.read_text()+'-bit\n');shutil.copyfile(__file__,out/'source.py.txt')
    python=ROOT/'binary_audit/flow/runtime/bin/python.exe';start=time.monotonic();rc=-1
    with (out/'worker.log').open('w') as log:
        try:rc=subprocess.run([str(python),str(Path(__file__).resolve()),'--worker','--out',str(out)],cwd=ROOT,env=dict(os.environ,PYTHONHOME=sys.base_prefix),stdout=log,stderr=subprocess.STDOUT,timeout=480,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0)).returncode
        except subprocess.TimeoutExpired:pass
    m=dict(status='captured' if rc==0 else 'failed',returncode=rc,wall_seconds=time.monotonic()-start,dll_sha256=DLL_SHA,source_sha256=sha(out/'source.py.txt'),vendor_pnr_executed=False,open_chip_database_used=False)
    if rc==0:m['audit_sha256']=sha(out/'audit.json')
    (out/'manifest.json').write_text(json.dumps(m,indent=2));print(json.dumps(m,indent=2))
    if rc:raise SystemExit(1)

if __name__=='__main__':main()
