"""Recover native dedicated LUT5..8 mux pin bindings without vendor P&R."""
import argparse,json,math,os,shutil,struct,subprocess,sys,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from gowin_probe import prepare
from run_abc_reproduction import sha
from capture_runtime_tables import EXPECTED


def fixture():
    lines=['module top(a,q); input [7:0] a; output q; wire [7:0] x; wire [15:0] f; wire [7:0] m5; wire [3:0] m6; wire [1:0] m7; wire m8;']
    for i in range(8):lines.append(f'IBUF ib{i}(.I(a[{i}]),.O(x[{i}]));')
    for i in range(16):
        lines.extend([f'LUT4 lut_{i}(.I0(x[0]),.I1(x[1]),.I2(x[2]),.I3(x[3]),.F(f[{i}]));',f"defparam lut_{i}.INIT=16'h{(0xA639^(i*0x131))&0xffff:04X};"])
    for level,count in ((5,8),(6,4),(7,2),(8,1)):
        previous='f' if level==5 else 'm'+str(level-1)
        for i in range(count):
            output=f'm{level}[{i}]' if level<8 else 'm8'
            lines.append(f'MUX2_LUT{level} mux_{level}_{i}(.I0({previous}[{2*i}]),.I1({previous}[{2*i+1}]),.S0(x[{level-1}]),.O({output}));')
    return '\n'.join(lines+['OBUF ob(.I(m8),.O(q));','endmodule'])+'\n'


def worker(out):
    import ctypes as C
    from native_device_session import NativeDeviceSession
    from ownpr_device import Device
    n=NativeDeviceSession(out);fn=n.fn;ptr=n.ptr;vp=C.c_void_p;d=Device()
    design=fn('?getDesign@globalMem@@QEAAPEAVDesign@NETLIST@@XZ',vp,[vp])(n.mem);vec=(C.c_uint64*3)()
    fn('?getHierAllInsts@Design@NETLIST@@QEAAXAEAV?$vector@PEAVInstance@NETLIST@@V?$allocator@PEAVInstance@NETLIST@@@std@@@std@@@Z',None,[vp,vp])(design,C.addressof(vec))
    getname=fn('?getName@Instance@NETLIST@@QEAA?AV?$basic_string@DU?$char_traits@D@std@@V?$allocator@D@2@@std@@XZ',vp,[vp,vp]);gettype=fn('?getType@Instance@NETLIST@@QEAA?AW4INS_TYPE@2@XZ',C.c_int,[vp])
    def string(buf):
        p=C.addressof(buf);size=ptr(p+16)
        if size>4096:raise ValueError('Invalid native mux name')
        return C.string_at(ptr(p) if ptr(p+24)>=16 else p,size).decode()
    objects={}
    for a in range(vec[0],vec[1],8):
        obj=ptr(a);buf=C.create_string_buffer(32);getname(obj,C.addressof(buf));objects[string(buf)]=obj
    expected={f'mux_{level}_{i}' for level,count in ((5,8),(6,4),(7,2),(8,1)) for i in range(count)}
    if not expected<=objects.keys():raise ValueError('Native reader changed the mux fixture')
    setphy=fn('?setPhyInst@Instance@NETLIST@@QEAAXPEAVinstance@2@@Z',None,[vp,vp]);setlogic=fn('?setLogicInst@instance@NETLIST@@QEAAXPEAVInstance@2@@Z',None,[vp,vp]);bind=fn('?setPin2Node@Mux2@NETLIST@@UEAAXXZ',None,[vp])
    report=fn('?getPinNameForRpt@Pin@NETLIST@@QEAAXAEAV?$basic_string@DU?$char_traits@D@std@@V?$allocator@D@2@@std@@@Z',None,[vp,vp])
    samples=[];representatives={}
    for r in range(1,110):
        for c in range(1,183):
            if C.c_int.from_address(n.getgrid(n.chip,r,c)+0x18).value==5:representatives.setdefault(d.grid[r-1][c-1],(r,c))
    for typ,(r,c) in sorted(representatives.items()):
        aggregate=ptr(n.getgrid(n.chip,r,c)+0x70)
        for slot in range(8):
            phy=ptr(aggregate+0x150+8*slot);header=list(struct.unpack('<4h',C.string_at(phy+0x10,8)));level={33:5,34:6,35:7,36:8}.get(header[0])
            if level is None or header[1:]!=[r,c,slot]:raise ValueError('Unexpected physical mux hierarchy')
            obj=objects[f'mux_{level}_0'];setphy(obj,phy);setlogic(phy,obj);bind(obj);pins=[];queries=[]
            for direction in ('In','Out'):
                pvec=(C.c_uint64*3)();fn(f'?getAll{direction}Pins@Mux2@NETLIST@@UEAA?AV?$vector@PEAVPin@NETLIST@@V?$allocator@PEAVPin@NETLIST@@@std@@@std@@XZ',vp,[vp,vp])(obj,C.addressof(pvec))
                for address in range(pvec[0],pvec[1],8):
                    pin=ptr(address);buf=C.create_string_buffer(32);C.c_uint64.from_address(C.addressof(buf)+24).value=15;report(pin,C.addressof(buf));node=ptr(pin+0x18)
                    coordinate=list(struct.unpack('<3h',C.string_at(node,6))) if node else None
                    pins.append(dict(pin=string(buf).split('.',1)[1],direction=direction,native_pin_id=C.c_int.from_address(pin+0x48).value,
                        node=coordinate,node_name=d.names.get(coordinate[2]) if coordinate else None))
                    if direction=='In' and level<=7:
                        query=C.CFUNCTYPE(None,vp,vp,vp,vp,C.c_int,C.c_int)(n.dll._handle+0x1462550)
                        for selector in range(3):
                            values=(C.c_float*4)(*[float('nan')]*4);query(n.library,C.addressof(values),obj,pin,selector,n.kind)
                            if not all(math.isfinite(v) for v in values):raise ValueError('Incomplete standalone mux delay query')
                            queries.append(dict(pin=pins[-1]['pin'],selector=selector,values=list(values)))
            samples.append(dict(level=level,tile_type=typ,slot=slot,site=[r,c],logical_type=gettype(obj),physical_header=header,pins=pins,
                queries=queries,physical_cached_logic=hex(ptr(phy+0x18))))
            setphy(obj,None);setlogic(phy,None)
    (out/'mux_bindings.json').write_text(json.dumps(samples,indent=2));print('MUX_BINDINGS',len(samples),'TILE_TYPES',len(representatives),flush=True)


def main():
    p=argparse.ArgumentParser();p.add_argument('--out',type=Path,required=True);p.add_argument('--worker',action='store_true');a=p.parse_args();out=a.out.resolve()
    if a.worker:return worker(out)
    prepare(out);(out/'probe.vg').write_text(fixture());shutil.copyfile(__file__,out/'capture_source.py.txt');python=ROOT/'binary_audit/flow/runtime/bin/python.exe';start=time.monotonic();rc=None;expired=False
    with (out/'worker.log').open('w') as log:
        try:rc=subprocess.run([str(python),str(Path(__file__).resolve()),'--worker','--out',str(out)],cwd=python.parent,env=dict(os.environ,PYTHONHOME=sys.base_prefix),stdout=log,stderr=subprocess.STDOUT,timeout=120).returncode
        except subprocess.TimeoutExpired:expired=True
    result=dict(status='captured' if rc==0 and (out/'mux_bindings.json').exists() else 'failed',returncode=rc,timeout=expired,wall_seconds=time.monotonic()-start,dll_sha256=EXPECTED,
        source_sha256=sha(out/'probe.vg'),capture_source_sha256=sha(out/'capture_source.py.txt'),vendor_pnr_executed=False,open_chip_database_used=False)
    if (out/'mux_bindings.json').exists():result['bindings_sha256']=sha(out/'mux_bindings.json')
    (out/'manifest.json').write_text(json.dumps(result,indent=2));print(json.dumps(result,indent=2))
    if result['status']!='captured':raise SystemExit(1)


if __name__=='__main__':main()
