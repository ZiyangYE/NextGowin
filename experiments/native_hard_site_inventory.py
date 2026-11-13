"""Read unplaced BRAM/DSP site inventory through native device getters.

Observed aggregate members are evidence for further binding experiments, not
an enabled hard-resource packer or a configuration/clock/timing proof.
"""
import argparse,json,os,shutil,struct,subprocess,sys,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from gowin_probe import prepare
from ownpr_device import DLL_SHA
from ownpr_place import sha
from workspace_budget import reserve


def worker(out):
    import ctypes as C
    from native_device_session import NativeDeviceSession
    from ownpr_device import Device
    native=NativeDeviceSession(out);device=Device();vp=C.c_void_p;ptr=native.ptr
    query=C.windll.kernel32.VirtualQuery;query.argtypes=[vp,vp,C.c_size_t];query.restype=C.c_size_t
    def typename(p):
        info=C.create_string_buffer(48)
        if not p or not query(p,info,48):return None
        state,protect=struct.unpack_from('<II',info,32)
        if state!=0x1000 or protect&0x101:return None
        vt=ptr(p);base=native.dll._handle
        if not base<=vt<base+0x2000000:return None
        loc=ptr(vt-8)
        if not base<=loc<base+0x2000000:return None
        rva=C.c_uint32.from_address(loc+12).value
        if rva>=0x2000000:return None
        return C.string_at(base+rva+16).decode('ascii')
    bram=native.fn('?getBsramByLoc@chip@NETLIST@@QEAAPEAVbsram@2@FFF@Z',vp,[vp,C.c_short,C.c_short,C.c_short])
    dsp=native.fn('?getNewDspByLoc@chip@NETLIST@@QEAAPEAVnewDsp@2@FF@Z',vp,[vp,C.c_short,C.c_short])
    objects={};rows=[];grid_counts={}
    for r in range(1,110):
        for c in range(1,183):
            grid=native.getgrid(native.chip,r,c)
            if not grid:continue
            kind=C.c_int.from_address(grid+0x18).value;grid_counts[kind]=grid_counts.get(kind,0)+1
            queries=[('bsram',slot,bram(native.chip,r,c,slot)) for slot in (0,1)] if kind in (6,10) else [('new_dsp',None,dsp(native.chip,r,c))] if kind==7 else []
            for family,slot,pointer in queries:
                if not pointer:continue
                alias=dict(native_row=r,native_column=c,slot_argument=slot,fse_type=device.grid[r-1][c-1])
                if pointer in objects:rows[objects[pointer]]['lookup_aliases'].append(alias);continue
                row=dict(id=len(rows),family=family,object_class=typename(pointer),lookup_aliases=[alias])
                if family=='bsram':
                    row['site_header']=list(struct.unpack('<4h',C.string_at(pointer+0x10,8)))
                    if row['object_class']!='.?AVbsram@NETLIST@@':raise ValueError('Unexpected native memory-site type')
                else:
                    members=[]
                    for offset in range(0,0x100,8):
                        child=ptr(pointer+offset);typ=typename(child)
                        if typ:members.append(dict(offset=offset,object_class=typ,site_header=list(struct.unpack('<4h',C.string_at(child+0x10,8)))))
                    row['observed_typed_members']=members;row['member_scan_is_exhaustive']=False
                objects[pointer]=len(rows);rows.append(row)
    result=dict(status='native_unplaced_hard_site_inventory',sites=rows,native_grid_counts=grid_counts,dll_sha256=DLL_SHA,source_fse_sha256=device.fse.sha256,
        vendor_placement_executed=False,vendor_routing_executed=False,open_chip_database_used=False,logical_binding_verified=False,configuration_verified=False,timing_signoff=False)
    (out/'hard_sites.json').write_text(json.dumps(result,indent=2));print('SITES',len(rows),flush=True)


def main():
    p=argparse.ArgumentParser();p.add_argument('--out',type=Path,required=True);p.add_argument('--worker',action='store_true');a=p.parse_args();out=a.out.resolve()
    if a.worker:return worker(out)
    storage=reserve(ROOT,50_000_000);prepare(out);shutil.copyfile(__file__,out/'source.py.txt');python=ROOT/'binary_audit/flow/runtime/bin/python.exe';start=time.monotonic()
    with (out/'worker.log').open('w') as log:
        try:code=subprocess.run([str(python),str(Path(__file__).resolve()),'--worker','--out',str(out)],cwd=python.parent,env=dict(os.environ,PYTHONHOME=sys.base_prefix),stdout=log,stderr=subprocess.STDOUT,timeout=120).returncode
        except subprocess.TimeoutExpired:code=-1
    r=dict(status='captured' if code==0 else 'failed',returncode=code,wall_seconds=time.monotonic()-start,dll_sha256=DLL_SHA,vendor_pnr_executed=False,storage_reservation=storage)
    if code==0:r['sites_sha256']=sha(out/'hard_sites.json')
    (out/'manifest.json').write_text(json.dumps(r,indent=2));print(json.dumps(r,indent=2))
    if code:raise SystemExit(1)


if __name__=='__main__':main()
