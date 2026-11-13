"""Read native CFU arithmetic object inventory without vendor P&R."""
import argparse,json,os,shutil,struct,subprocess,sys,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from gowin_probe import prepare
from run_abc_reproduction import sha
from capture_runtime_tables import EXPECTED


def worker(out):
    from native_device_session import NativeDeviceSession
    import ctypes as C
    native=NativeDeviceSession(out);ptr=native.ptr;base=native.dll._handle
    query=C.windll.kernel32.VirtualQuery;query.argtypes=[C.c_void_p,C.c_void_p,C.c_size_t];query.restype=C.c_size_t
    def readable(p):
        info=C.create_string_buffer(48)
        if not p or not query(p,info,48):return False
        state,protect=struct.unpack_from('<II',info,32)
        return state==0x1000 and not protect&0x101
    def typename(p):
        if not readable(p):return None
        vt=ptr(p)
        if not base<=vt<base+0x2000000:return None
        loc=ptr(vt-8)
        if not base<=loc<base+0x2000000:return None
        offset=C.c_uint32.from_address(loc+12).value
        if offset>=0x2000000:return None
        return C.string_at(base+offset+16).decode('ascii')
    grid=native.getgrid(native.chip,50,90);aggregate=ptr(grid+0x70);rows=[]
    for offset in range(0,0x318,8):
        address=ptr(aggregate+offset);typ=typename(address)
        if typ:rows.append(dict(offset=offset,kind=typ,header=list(struct.unpack('<4h',C.string_at(address+0x10,8)))))
    (out/'cfu_objects.json').write_text(json.dumps(rows,indent=2));print('CFU_OBJECTS',len(rows),flush=True)
    if (out/'alu_fixture.json').exists():
        from ownpr_device import Device
        device=Device();fn=native.fn;vp=C.c_void_p
        design=fn('?getDesign@globalMem@@QEAAPEAVDesign@NETLIST@@XZ',vp,[vp])(native.mem);vec=(C.c_uint64*3)()
        fn('?getHierAllInsts@Design@NETLIST@@QEAAXAEAV?$vector@PEAVInstance@NETLIST@@V?$allocator@PEAVInstance@NETLIST@@@std@@@std@@@Z',None,[vp,vp])(design,C.addressof(vec))
        namefn=fn('?getName@Instance@NETLIST@@QEAA?AV?$basic_string@DU?$char_traits@D@std@@V?$allocator@D@2@@std@@XZ',vp,[vp,vp]);objects={}
        def name(obj):
            buf=C.create_string_buffer(32);namefn(obj,C.addressof(buf));p=C.addressof(buf);n=ptr(p+16)
            return C.string_at(ptr(p) if ptr(p+24)>=16 else p,n).decode()
        ties={}
        for address in range(vec[0],vec[1],8):
            obj=ptr(address)
            if typename(obj)=='.?AVAlu@NETLIST@@':objects[name(obj)]=obj
            if typename(obj) in ('.?AVVcc@NETLIST@@','.?AVGnd@NETLIST@@'):
                cls='Vcc' if typename(obj)=='.?AVVcc@NETLIST@@' else 'Gnd';pins=(C.c_uint64*3)()
                fn(f'?getAllOutPins@{cls}@NETLIST@@UEAA?AV?$vector@PEAVPin@NETLIST@@V?$allocator@PEAVPin@NETLIST@@@std@@@std@@XZ',vp,[vp,vp])(obj,C.addressof(pins))
                if pins[1]-pins[0]!=8:raise ValueError('Invalid tie fixture')
                ties[int(cls=='Vcc')]=ptr(ptr(pins[0])+8)
        if set(objects)!={f'alu_{m}' for m in range(9)}:raise ValueError('Native ALU fixture instance set changed')
        setphy=fn('?setPhyInst@Instance@NETLIST@@QEAAXPEAVinstance@2@@Z',None,[vp,vp]);setlogic=fn('?setLogicInst@instance@NETLIST@@QEAAXPEAVInstance@2@@Z',None,[vp,vp])
        bind=fn('?setPin2Node@Alu@NETLIST@@UEAAXXZ',None,[vp]);getpin=fn('?getOnePin@Alu@NETLIST@@UEAAPEAVPin@2@I@Z',vp,[vp,C.c_uint])
        selected={}
        for r in range(1,110):
            for c in range(1,183):
                g=native.getgrid(native.chip,r,c)
                if C.c_int.from_address(g+0x18).value==5:selected.setdefault(device.grid[r-1][c-1],(r,c))
        samples=[]
        for typ,(r,c) in sorted(selected.items()):
            aggregate=ptr(native.getgrid(native.chip,r,c)+0x70)
            for slot in range(6):
                phy=ptr(aggregate+0x40+8*slot)
                if typename(phy)!='.?AValu@NETLIST@@':raise ValueError('Missing native ALU slot')
                for mode,obj in sorted(objects.items()):
                    setphy(obj,phy);setlogic(phy,obj);bind(obj);pins=[]
                    for index in range(6):
                        pin=getpin(obj,index);node=ptr(pin+0x18) if pin else None
                        point=list(struct.unpack('<3h',C.string_at(node,6))) if node else None
                        fanouts=[]
                        if point and index==4:
                            fanouts=[dict(node=list(n),name=device.names.get(n[2])) for n in native.fanouts(tuple(point))]
                        pins.append(dict(pin_index=index,node=point,node_name=device.names.get(point[2]) if point else None,fanouts=fanouts))
                    samples.append(dict(tile_type=typ,site=[r,c],slot=slot,mode=int(mode.split('_')[1]),pins=pins))
                setlogic(phy,None)
        (out/'alu_bindings.json').write_text(json.dumps(samples,indent=2));print('ALU_BINDINGS',len(samples),'TYPES',len(selected),flush=True)
        if (out/'encode_mode2.json').exists():
            from native_alu_encoding import probe
            probe(native,out,objects,ties)
        if not ptr(native.mem+0x18):
            (out/'carry_probe_status.json').write_text(json.dumps(dict(status='requires_native_carry_ownership_metadata',
                ordinary_cout_fanout_zero=True,carry_connectivity_verified=False,
                evidence='isDirectConn 0x2f014f queries PlaceRoute/P2R ownership before testing adjacent slots; no P&R object is created by this data-only session.'),indent=2))
            return
        direct=fn('?isDirectConn@Pin@NETLIST@@QEAA_NPEAVglobalMem@@@Z',C.c_bool,[vp,vp]);a=objects['alu_0'];b=objects['alu_1']
        cin=getpin(b,3);original_net=ptr(cin+8);C.c_uint64.from_address(cin+8).value=ptr(getpin(a,4)+8)
        cases=[]
        for sr,sc in ((50,90),(2,2),(109,181)):
            source_grid=native.getgrid(native.chip,sr,sc)
            if C.c_int.from_address(source_grid+0x18).value!=5:continue
            for sa in range(6):
                pa=ptr(ptr(source_grid+0x70)+0x40+sa*8);setphy(a,pa);setlogic(pa,a);bind(a)
                for dr,dc in ((0,0),(0,1),(0,-1),(1,0),(-1,0),(0,2),(0,-2),(2,0),(-2,0)):
                    r,c=sr+dr,sc+dc
                    if not 1<=r<110 or not 1<=c<183:continue
                    grid=native.getgrid(native.chip,r,c)
                    if C.c_int.from_address(grid+0x18).value!=5:continue
                    for sb in range(6):
                        if (r,c,sb)==(sr,sc,sa):continue
                        pb=ptr(ptr(grid+0x70)+0x40+sb*8);setphy(b,pb);setlogic(pb,b);bind(b)
                        cases.append(dict(source=[sr,sc,sa],destination=[r,c,sb],direct=bool(direct(cin,native.mem))))
                        setlogic(pb,None)
                setlogic(pa,None)
        C.c_uint64.from_address(cin+8).value=original_net
        (out/'carry_direct_connections.json').write_text(json.dumps(cases,indent=2));print('DIRECT_CARRY',sum(r['direct'] for r in cases),'OF',len(cases),flush=True)


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--out',type=Path,required=True);ap.add_argument('--worker',action='store_true');ap.add_argument('--bind',action='store_true');ap.add_argument('--encode-mode2',action='store_true');ap.add_argument('--profiles',type=Path);a=ap.parse_args();out=a.out.resolve()
    if a.worker:return worker(out)
    from workspace_budget import reserve
    reserve(ROOT,400_000_000)
    prepare(out)
    if a.profiles:
        if not a.encode_mode2:ap.error('--profiles requires --encode-mode2')
        shutil.copyfile(a.profiles,out/'requested_profiles.json')
    if a.encode_mode2:
        if not a.bind:ap.error('--encode-mode2 requires --bind')
        (out/'encode_mode2.json').write_text('{}');cmd=out/'cmd.do';cmd.write_text(cmd.read_text()+'-bit\n')
    if a.bind:
        lines=['module top(a,q); input [3:0] a; output [19:0] q; wire [3:0] x; wire [17:0] z; wire one,zero;',
               'VCC tie1(.V(one)); GND tie0(.G(zero)); OBUF high_pad(.I(one),.O(q[18])); OBUF low_pad(.I(zero),.O(q[19]));']
        for i in range(4):lines.append(f'IBUF ib{i}(.I(a[{i}]),.O(x[{i}]));')
        for mode in range(9):
            lines.extend([f'ALU alu_{mode}(.I0(x[0]),.I1(x[1]),.I3(x[2]),.CIN(x[3]),.SUM(z[{2*mode}]),.COUT(z[{2*mode+1}]));',f'defparam alu_{mode}.ALU_MODE={mode};'])
        for i in range(18):lines.append(f'OBUF ob{i}(.I(z[{i}]),.O(q[{i}]));')
        lines.append('endmodule');(out/'probe.vg').write_text('\n'.join(lines));(out/'alu_fixture.json').write_text(json.dumps(dict(modes=list(range(9)))))
    shutil.copyfile(__file__,out/'capture_source.py.txt');python=ROOT/'binary_audit/flow/runtime/bin/python.exe';start=time.monotonic()
    with (out/'worker.log').open('w') as log:
        cp=subprocess.run([str(python),str(Path(__file__).resolve()),'--worker','--out',str(out)],cwd=python.parent,env=dict(os.environ,PYTHONHOME=sys.base_prefix),stdout=log,stderr=subprocess.STDOUT,timeout=240 if a.encode_mode2 else 120)
    meta=dict(status='captured' if cp.returncode==0 else 'failed',returncode=cp.returncode,wall_seconds=time.monotonic()-start,dll_sha256=EXPECTED,vendor_pnr_executed=False,open_chip_database_used=False)
    if (out/'cfu_objects.json').exists():meta['objects_sha256']=sha(out/'cfu_objects.json')
    if (out/'alu_bindings.json').exists():meta['bindings_sha256']=sha(out/'alu_bindings.json')
    if (out/'carry_direct_connections.json').exists():meta['carry_connections_sha256']=sha(out/'carry_direct_connections.json')
    (out/'manifest.json').write_text(json.dumps(meta,indent=2));print(json.dumps(meta,indent=2))
    if cp.returncode:raise SystemExit(1)


if __name__=='__main__':main()
