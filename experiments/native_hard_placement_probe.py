"""Rebind independently chosen hard sites, with no native placement/routing."""
import argparse,json,os,shutil,subprocess,sys,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from ownpr_image_storage import sha
from workspace_budget import reserve


def worker(out):
    from native_device_session import NativeDeviceSession
    import ctypes as C
    from native_hard_binding import HardSiteBinding
    from native_hard_resource_capture import capture
    native=NativeDeviceSession(out);fn=native.fn;ptr=native.ptr;vp=C.c_void_p;base=native.dll._handle
    request=json.loads((out/'request.json').read_text());placed=json.loads(Path(request['placement']).read_text());objects={}
    mapping=json.loads((out/'name_map.json').read_text()) if (out/'name_map.json').exists() else None
    reverse={alias:name for name,alias in mapping['instances'].items()} if mapping else {}
    def classname(obj):
        if not obj:return None
        vt=ptr(obj)
        if not base<=vt<base+0x2000000:raise ValueError('Unexpected native class')
        return C.string_at(base+C.c_uint32.from_address(ptr(vt-8)+12).value+16).decode()
    def string(p):
        n=ptr(p+16)
        if n>4096:raise ValueError('Unexpected native string')
        return C.string_at(ptr(p) if ptr(p+24)>=16 else p,n).decode()
    getname=fn('?getName@Instance@NETLIST@@QEAA?AV?$basic_string@DU?$char_traits@D@std@@V?$allocator@D@2@@std@@XZ',vp,[vp,vp])
    getphy=fn('?getPhyInst@Instance@NETLIST@@QEAAPEAVinstance@2@XZ',vp,[vp])
    nodes=fn('?getNodebyPin@Pin@NETLIST@@QEAA_NAEAV?$vector@PEAVnode@NETLIST@@V?$allocator@PEAVnode@NETLIST@@@std@@@std@@PEAVglobalMem@@@Z',C.c_bool,[vp,vp,vp])
    design=fn('?getDesign@globalMem@@QEAAPEAVDesign@NETLIST@@XZ',vp,[vp])(native.mem);vector=(C.c_uint64*3)()
    fn('?getHierAllInsts@Design@NETLIST@@QEAAXAEAV?$vector@PEAVInstance@NETLIST@@V?$allocator@PEAVInstance@NETLIST@@@std@@@std@@@Z',None,[vp,vp])(design,C.addressof(vector))
    for address in range(vector[0],vector[1],8):
        obj=ptr(address);buf=C.create_string_buffer(32);getname(obj,C.addressof(buf));name=string(C.addressof(buf))
        original=reverse.get(name,name)
        if original in placed['hard_clusters']:objects[original]=obj
    if set(objects)!=set(placed['hard_clusters']):raise ValueError('Hard instance identities differ from fixture')
    bindings=[]
    for name,obj in sorted(objects.items()):
        loc=placed['hard_clusters'][name];bindings.append(HardSiteBinding(native,obj,'bsram' if loc['family']=='bsram' else 'multalu27x18',loc['row']+1,loc['column']+1))
    array=(C.c_uint64*len(objects))(*[objects[n] for n in sorted(objects)]);begin=C.addressof(array)
    recorded=capture(out,fn,native.mem,(begin,begin+8*len(objects),begin+8*len(objects)),getname,getphy,classname,string,nodes,False)
    if mapping:
        for row in recorded['instances']:row['native_instance']=row['instance'];row['instance']=reverse[row['instance']]
        (out/'hard_resource_bindings.json').write_text(json.dumps(recorded,indent=2))
    for binding in reversed(bindings):binding.restore()
    (out/'restoration.json').write_text(json.dumps(dict(all_bindings_restored=True,hard_cells=len(bindings)),indent=2))


def main():
    p=argparse.ArgumentParser();p.add_argument('--placement',type=Path);p.add_argument('--out',type=Path,required=True);p.add_argument('--worker',action='store_true');a=p.parse_args();out=a.out.resolve()
    if a.worker:return worker(out)
    if not a.placement:p.error('--placement required')
    reserve(ROOT,50_000_000)
    from gowin_probe import prepare
    from ownpr_device import DLL_SHA
    prepare(out);folder=a.placement.resolve();placed=folder/'placement.json';packed=folder/'packed.json'
    from ownpr_native_netlist import emit
    from ownpr_hard_architecture import HardArchitecture
    design=json.loads(packed.read_text());text,mapping=emit(design,hard=HardArchitecture());vg=out/'probe.vg';vg.write_text(text)
    (out/'name_map.json').write_text(json.dumps(mapping,indent=2))
    request=dict(placement=str(placed),placement_sha256=sha(placed),packed=str(packed),packed_sha256=sha(packed),source_sha256=sha(vg),name_map_sha256=sha(out/'name_map.json'))
    (out/'request.json').write_text(json.dumps(request,indent=2));shutil.copyfile(__file__,out/'source.py.txt')
    python=ROOT/'binary_audit/flow/runtime/bin/python.exe';start=time.monotonic()
    with (out/'worker.log').open('w') as log:
        proc=subprocess.run([str(python),str(Path(__file__).resolve()),'--worker','--out',str(out)],cwd=python.parent,env=dict(os.environ,PYTHONHOME=sys.base_prefix),stdout=log,stderr=subprocess.STDOUT,timeout=120)
    result=dict(status='captured' if proc.returncode==0 else 'failed',returncode=proc.returncode,wall_seconds=time.monotonic()-start,dll_sha256=DLL_SHA,vendor_pnr_executed=False,request=request)
    if proc.returncode==0:
        if sha(placed)!=request['placement_sha256'] or sha(packed)!=request['packed_sha256']:raise ValueError('Placement input changed during capture')
        if sha(vg)!=request['source_sha256'] or sha(out/'name_map.json')!=request['name_map_sha256']:raise ValueError('Serialized hard fixture changed during capture')
        result.update(bindings_sha256=sha(out/'hard_resource_bindings.json'),binding_helper_sha256=sha(ROOT/'scripts/native_hard_binding.py'),restoration_sha256=sha(out/'restoration.json'))
    (out/'manifest.json').write_text(json.dumps(result,indent=2));print(json.dumps(result,indent=2))
    if proc.returncode:raise SystemExit(1)


if __name__=='__main__':main()
