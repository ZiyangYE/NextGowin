"""Isolated native LUT5/6 plus ordinary LUT capacity and fuse composition.

No vendor P&R or hardware programming. This does not certify routed timing,
FF sharing, arbitrary root slots, or enable production packing.
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
    from ownpr_ff_configuration import independent_ff_groups
    from ownpr_cls_configuration import ClsChoices
    from audit_native_encoding_probe import payload
    from native_mux_encoding_probe import tree_slots
    dev=Device();projection=NativeFrameProjection(dev);codec=LutInitChoices(dev);cls=ClsChoices(dev)
    vp=C.c_void_p;ptr=n.ptr;fn=n.fn;objects={};design=fn('?getDesign@globalMem@@QEAAPEAVDesign@NETLIST@@XZ',vp,[vp])(n.mem);vec=(C.c_uint64*3)()
    fn('?getHierAllInsts@Design@NETLIST@@QEAAXAEAV?$vector@PEAVInstance@NETLIST@@V?$allocator@PEAVInstance@NETLIST@@@std@@@std@@@Z',None,[vp,vp])(design,C.addressof(vec))
    getname=fn('?getName@Instance@NETLIST@@QEAA?AV?$basic_string@DU?$char_traits@D@std@@V?$allocator@D@2@@std@@XZ',vp,[vp,vp])
    for address in range(vec[0],vec[1],8):
        obj=ptr(address);buf=C.create_string_buffer(32);getname(obj,C.addressof(buf));p=C.addressof(buf);length=ptr(p+16)
        if length>64:raise ValueError('Invalid fixture name')
        objects[C.string_at(ptr(p) if ptr(p+24)>=16 else p,length).decode()]=obj
    setphy=fn('?setPhyInst@Instance@NETLIST@@QEAAXPEAVinstance@2@@Z',None,[vp,vp]);setlogic=fn('?setLogicInst@instance@NETLIST@@QEAAXPEAVInstance@2@@Z',None,[vp,vp])
    bind={k:fn(f'?setPin2Node@{k}@NETLIST@@UEAAXXZ',None,[vp]) for k in ('Lut4','Mux2')}
    setinit=fn('?setInitValueForRouted@Lut4@NETLIST@@QEAAXV?$basic_string@DU?$char_traits@D@std@@V?$allocator@D@2@@std@@@Z',None,[vp,vp]);encode=fn('?generateFusemap@@YAHPEAVglobalMem@@@Z',C.c_int,[vp]);fs=out/'probe.fs'
    for obj in objects.values():setphy(obj,None)
    for _ in range(2):
        if encode(n.mem):raise ValueError('Baseline encoding failed')
    baseline=projection.read(fs);base_payload=payload(fs)
    def save(label):
        target=out/(label+'.fs.gz')
        with fs.open('rb') as src,gzip.open(target,'wb') as dst:shutil.copyfileobj(src,dst)
        return target.name
    baseline_name=save('baseline');sites=[(50,90),next((r,c) for r in range(51,55) for c in range(90,95) if dev.grid[r-1][c-1]==17)];records=[]
    for r,c in sites:
        if dev.grid[r-1][c-1]!=17:raise ValueError('Uncalibrated tile')
        agg=ptr(n.getgrid(n.chip,r,c)+0x70);groups=[ptr(agg+0x360+8*i) for i in range(4)]
        saved_groups=[C.string_at(p+6,38) for p in groups];saved_words=[C.string_at(ptr(agg+8*i)+0x20,3) for i in range(8)];saved_flags=C.string_at(agg+0x318,0x48)
        for level in (5,6):
            muxes,leaves=tree_slots(level);slots={s for _,s in leaves};extras=[(f'lut_{8+s}',s) for s in range(8) if s not in slots]
            for label,use_wide,use_lut in (('wide',True,False),('ordinary',False,True),('mixed',True,True)):
                active_leaves=(leaves if use_wide else [])+(extras if use_lut else []);active_muxes=muxes if use_wide else [];attrs=independent_ff_groups([]);requirements={};bindings={}
                for _,s in active_leaves:attrs[s//2][0]=9
                for i,(group,values) in enumerate(zip(groups,attrs)):
                    C.memmove(group+6,struct.pack('<19h',*values),38);requirements.update(cls.requirements(r-1,c-1,i,values))
                for name,slot in active_leaves:
                    obj=objects[name];phy=ptr(agg+8*slot);setphy(obj,phy);setlogic(phy,obj)
                    word=(0xA639^(int(name.split('_')[-1])*0x131))&65535;text=f"16'h{word:04X}".encode();arg=C.create_string_buffer(32);C.memmove(arg,text,len(text));C.c_uint64.from_address(C.addressof(arg)+16).value=len(text);C.c_uint64.from_address(C.addressof(arg)+24).value=15;setinit(obj,C.addressof(arg))
                    C.c_ushort.from_address(phy+0x20).value=word;C.c_ubyte.from_address(phy+0x22).value=0;C.c_ubyte.from_address(agg+0x326+slot).value=1;bind['Lut4'](obj)
                    req=codec.requirements(r-1,c-1,slot,word)
                    if any(b in requirements and requirements[b]!=v for b,v in req.items()):raise ValueError('Fuse collision')
                    requirements.update(req);bindings[name]='Lut4'
                for name,slot in reversed(active_muxes):
                    obj=objects[name];phy=ptr(agg+0x150+8*slot);setphy(obj,phy);setlogic(phy,obj);C.c_uint64.from_address(phy+0x18).value=obj;bind['Mux2'](obj);bindings[name]='Mux2'
                pins={}
                for name,kind in bindings.items():
                    pins[name]=[]
                    for direction in ('In','Out'):
                        vector=(C.c_uint64*3)();fn(f'?getAll{direction}Pins@{kind}@NETLIST@@UEAA?AV?$vector@PEAVPin@NETLIST@@V?$allocator@PEAVPin@NETLIST@@@std@@@std@@XZ',vp,[vp,vp])(objects[name],C.addressof(vector))
                        for address in range(vector[0],vector[1],8):
                            node=ptr(ptr(address)+0x18);pins[name].append(dict(direction=direction,node=list(struct.unpack('<3h',C.string_at(node,6))) if node else None))
                    if len(pins[name])!=(5 if kind=='Lut4' else 4):raise ValueError('Incomplete pin inventory')
                if encode(n.mem):raise ValueError('Encoding failed')
                image=projection.read(fs);bad=[list(b) for b,v in requirements.items() if int(image[projection.index(b)])-48!=v]
                record=dict(site=[r,c],tile_type=17,level=level,label=label,ordinary_slots=sorted(set(range(8))-slots),leaf_slots=sorted(slots),variant=save(f'{r}_{c}_{level}_{label}'),pins=pins,requirements=len(requirements),mismatches=bad)
                for name,slot in active_muxes:
                    phy=ptr(agg+0x150+8*slot);setphy(objects[name],None);fn('?setPhyMux2@Mux2@NETLIST@@QEAAXPEAVmux2@2@@Z',None,[vp,vp])(objects[name],None);setlogic(phy,None);C.c_uint64.from_address(phy+0x18).value=0
                for name,slot in active_leaves:
                    setphy(objects[name],None);fn('?setPhyLut4@Lut4@NETLIST@@QEAAXPEAVlut4@2@@Z',None,[vp,vp])(objects[name],None);setlogic(ptr(agg+8*slot),None)
                for group,raw in zip(groups,saved_groups):C.memmove(group+6,raw,38)
                for i,raw in enumerate(saved_words):C.memmove(ptr(agg+8*i)+0x20,raw,3)
                C.memmove(agg+0x318,saved_flags,len(saved_flags))
                if encode(n.mem):raise ValueError('Restore encoding failed')
                record['full_payload_restored']=payload(fs)==base_payload;records.append(record);(out/'capture.json').write_text(json.dumps(dict(baseline=baseline_name,records=records),indent=2));print('CASE',r,c,level,label,len(bad),record['full_payload_restored'],flush=True)
                if bad or not record['full_payload_restored']:raise ValueError('Native mixed encoding mismatch')
            rows={x['label']:x for x in records if x['site']==[r,c] and x['level']==level}
            if {**rows['wide']['pins'],**rows['ordinary']['pins']}!=rows['mixed']['pins']:raise ValueError('Sharing changed pin bindings')
            w=projection.read(out/rows['wide']['variant']);o=projection.read(out/rows['ordinary']['variant']);m=projection.read(out/rows['mixed']['variant'])
            if not np.array_equal(m,np.where(w!=baseline,w,o)):raise ValueError('Unexpected shared bitmap interaction')
            def nodes(names,direction):return {tuple(p['node']) for name in names for p in rows['mixed']['pins'][name] if p['direction']==direction and p['node'] is not None}
            if nodes([n for n,_ in extras],'In')&nodes([n for n,_ in leaves]+[n for n,_ in muxes],'In'):raise ValueError('Ordinary and wide external input nodes overlap')
            if nodes([n for n,_ in extras],'Out')&nodes([muxes[0][0]],'Out'):raise ValueError('Ordinary and wide root outputs overlap')
    (out/'audit.json').write_text(json.dumps(dict(status='isolated_wide_lut_composition_verified',sites=sites,levels=[5,6],tile_type=17,requirements_match=True,pin_bindings_unchanged=True,external_input_output_nodes_disjoint=True,bitmap_composition_match=True,full_payload_restored=True,dll_sha256=DLL_SHA,fse_sha256=dev.fse.sha256,capture_sha256=sha(out/'capture.json'),production_enabled=False,ff_sharing_supported=False,full_route_sta_verified=False,hardware_verified=False),indent=2))


def main():
    p=argparse.ArgumentParser();p.add_argument('--out',type=Path,required=True);p.add_argument('--worker',action='store_true');a=p.parse_args();out=a.out.resolve()
    if a.worker:return worker(out)
    from workspace_budget import reserve
    from gowin_probe import prepare
    from ownpr_device import DLL_SHA
    from native_mux_inventory import fixture
    reserve(ROOT,150_000_000);prepare(out);(out/'probe.vg').write_text(fixture());cmd=out/'cmd.do';cmd.write_text(cmd.read_text()+'-bit\n');shutil.copyfile(__file__,out/'source.py.txt')
    python=ROOT/'binary_audit/flow/runtime/bin/python.exe';start=time.monotonic();rc=-1;expired=False
    with (out/'worker.log').open('w') as log:
        try:rc=subprocess.run([str(python),str(Path(__file__).resolve()),'--worker','--out',str(out)],cwd=ROOT,env=dict(os.environ,PYTHONHOME=sys.base_prefix),stdout=log,stderr=subprocess.STDOUT,timeout=480,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0)).returncode
        except subprocess.TimeoutExpired:expired=True
    result=dict(status='captured' if rc==0 else 'failed',returncode=rc,timeout=expired,wall_seconds=time.monotonic()-start,dll_sha256=DLL_SHA,source_sha256=sha(out/'source.py.txt'),fixture_sha256=sha(out/'probe.vg'),vendor_pnr_executed=False,open_chip_database_used=False,device_programmed=False,production_enabled=False)
    if rc==0:result['audit_sha256']=sha(out/'audit.json')
    (out/'manifest.json').write_text(json.dumps(result,indent=2));print(json.dumps(result,indent=2))
    if rc:raise SystemExit(1)


if __name__=='__main__':main()
