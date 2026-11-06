"""Native FF-control attribute oracle; no placement or routing algorithms.

Only isolated logical/physical test objects are rebound. This is a semantic
calibration fixture, not a routed netlist or a functionally validated image.
"""
import argparse,itertools,json,os,shutil,struct,subprocess,sys,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from gowin_probe import BIN,prepare
from capture_runtime_tables import EXPECTED
from run_abc_reproduction import sha


def worker(out,paired=False,multi=False,shared=False,shared_paired=False):
    handles=[os.add_dll_directory(str(p)) for p in (Path(sys.base_prefix)/'DLLs',Path(sys.base_prefix)/'Library/bin',BIN)]
    import ctypes as C
    C.windll.kernel32.SetErrorMode(0x8003)
    if sha(BIN/'gowin.dll')!=EXPECTED:raise ValueError('Unreviewed native ABI')
    os.chdir(out);dll=C.CDLL(str(BIN/'gowin.dll'));vp=C.c_void_p
    def fn(name,result,args):
        f=getattr(dll,name);f.restype=result;f.argtypes=args;return f
    def ptr(address):return C.c_uint64.from_address(address).value
    def string(address):
        n=C.c_uint64.from_address(address+16).value;cap=C.c_uint64.from_address(address+24).value
        if n>256:raise ValueError('Unexpected native string')
        return C.string_at(ptr(address) if cap>=16 else address,n).decode()
    args=[str(BIN/'gowin.exe'),'-do',str(out/'cmd.do')];av=(C.c_char_p*len(args))(*(s.encode() for s in args))
    if fn('?readInUserCommand@@YAHHPEAPEAD_N@Z',C.c_int,[C.c_int,C.POINTER(C.c_char_p),C.c_bool])(len(args),av,True):raise ValueError('Native argument parsing failed')
    mem=fn('?getGlobalMemPtr@@YAPEAVglobalMem@@XZ',vp,[])()
    for name in ('?readDevice@NETLIST@@YAHPEAVglobalMem@@@Z','?readConfigAttFile@@YAHPEAVglobalMem@@@Z','?readInput@VLGPARSE@@YAHPEAVglobalMem@@@Z'):
        if fn(name,C.c_int,[vp])(mem):raise ValueError('Native initialization failed: '+name)
    design=fn('?getDesign@globalMem@@QEAAPEAVDesign@NETLIST@@XZ',vp,[vp])(mem)
    vec=(C.c_uint64*3)();fn('?getHierAllInsts@Design@NETLIST@@QEAAXAEAV?$vector@PEAVInstance@NETLIST@@V?$allocator@PEAVInstance@NETLIST@@@std@@@std@@@Z',None,[vp,vp])(design,C.addressof(vec))
    getname=fn('?getName@Instance@NETLIST@@QEAA?AV?$basic_string@DU?$char_traits@D@std@@V?$allocator@D@2@@std@@XZ',vp,[vp,vp])
    instances={}
    for address in range(vec[0],vec[1],8):
        obj=ptr(address);buf=C.create_string_buffer(32);getname(obj,C.addressof(buf));instances[string(C.addressof(buf))]=obj
    print('INSTANCES',sorted(instances),flush=True)
    getpin=fn('?getOnePin@Reg@NETLIST@@UEAAPEAVPin@2@I@Z',vp,[vp,C.c_uint])
    gettype=fn('?getType@Instance@NETLIST@@QEAA?AW4INS_TYPE@2@XZ',C.c_int,[vp])
    setphy=fn('?setPhyInst@Instance@NETLIST@@QEAAXPEAVinstance@2@@Z',None,[vp,vp])
    bindpins=fn('?setPin2Node@Reg@NETLIST@@UEAAXXZ',None,[vp])
    direct=fn('?isDirectConn@Pin@NETLIST@@QEAA_NPEAVglobalMem@@@Z',C.c_bool,[vp,vp])
    getlutpin=fn('?getOnePin@Lut4@NETLIST@@UEAAPEAVPin@2@I@Z',vp,[vp,C.c_uint])
    routepins=fn('?getNodebyPin@Pin@NETLIST@@QEAA_NAEAV?$vector@PEAVnode@NETLIST@@V?$allocator@PEAVnode@NETLIST@@@std@@@std@@PEAVglobalMem@@@Z',C.c_bool,[vp,vp,vp])
    chip=fn('?getDevChip@globalMem@@QEAAPEAVchip@NETLIST@@XZ',vp,[vp])(mem)
    grid=fn('?getGridByRowCol@chip@NETLIST@@QEAAPEAVgrid@2@FF@Z',vp,[vp,C.c_short,C.c_short])(chip,50,90)
    if C.c_int.from_address(grid+0x18).value!=5:raise ValueError('Calibration site is not a CFU')
    aggregate=ptr(grid+0x70);groups=[ptr(aggregate+0x360+8*g) for g in range(4)]
    defaults=[C.string_at(p+6,38) for p in groups]
    constructor=C.CFUNCTYPE(vp,vp,vp,C.c_bool)(dll._handle+0x4f8d40)
    storage=C.create_string_buffer(0x188);encoder=constructor(C.addressof(storage),mem,False)
    if encoder!=C.addressof(storage) or C.c_int.from_address(encoder+0xe0).value!=70:raise ValueError('Native encoder object mismatch')
    convert=C.CFUNCTYPE(None,vp,vp)(dll._handle+0x558700)
    classify=C.CFUNCTYPE(C.c_int,vp,vp)(dll._handle+0x52b920)
    sentinel=instances['sentinel'];sources={'one':ptr(getpin(sentinel,2)+8),'zero':ptr(getpin(sentinel,4)+8)}
    records=[]
    pair_lut=instances.get('pair_lut')
    pair_net=ptr(getlutpin(pair_lut,8)+8) if paired else None
    for cell_type,control_pin in [('DFFRE',4),('DFFSE',3),('DFFCE',6),('DFFPE',5)]:
        obj=instances[cell_type.lower()];ce=getpin(obj,2);reset=getpin(obj,control_pin);clock=getpin(obj,1);data_pin=getpin(obj,0)
        if not ce or not reset:raise ValueError('Expected official FF control pin')
        ce_original=ptr(ce+8);reset_original=ptr(reset+8)
        data_original=ptr(data_pin+8)
        original_flags={p:C.c_ubyte.from_address(p+0x4f).value for p in (ce,reset,clock)}
        for slot in range(8):
            reg=ptr(aggregate+0xa0+8*slot);lut=ptr(aggregate+8*slot)
            setphy(obj,reg);C.c_uint64.from_address(reg+8).value=obj;bindpins(obj)
            C.c_ubyte.from_address(aggregate+0x318+slot).value=1
            C.c_ubyte.from_address(aggregate+0x326+slot).value=1
            C.c_ubyte.from_address(lut+0x22).value=0 if paired else 1
            if paired:
                setphy(pair_lut,lut);C.c_uint64.from_address(lut+8).value=pair_lut
                C.c_uint64.from_address(data_pin+8).value=pair_net
                C.c_ushort.from_address(lut+0x20).value=0x1234
            is_direct=direct(data_pin,mem);node_vector=(C.c_uint64*3)()
            route_return=routepins(data_pin,C.addressof(node_vector),mem)
            node_count=(node_vector[1]-node_vector[0])//8 if node_vector[0] else 0
            wrong_slot_direct=None
            if paired:
                setphy(pair_lut,ptr(aggregate+8*((slot+1)%8)))
                wrong_slot_direct=direct(data_pin,mem);setphy(pair_lut,lut)
            for ce_label in ('zero','one','signal','inverted'):
                for reset_label in ('zero','one','signal','inverted'):
                  for clock_label in ('signal','inverted'):
                    for address,raw in zip(groups,defaults):C.memmove(address+6,raw,38)
                    C.c_uint64.from_address(ce+8).value=ce_original if ce_label in ('signal','inverted') else sources[ce_label]
                    C.c_uint64.from_address(reset+8).value=reset_original if reset_label in ('signal','inverted') else sources[reset_label]
                    for p,label in ((ce,ce_label),(reset,reset_label),(clock,clock_label)):
                        C.c_ubyte.from_address(p+0x4f).value=(original_flags[p]&~0x40)|(0x40 if label=='inverted' else 0)
                    convert(encoder,grid)
                    data_routes=(C.c_uint64*3)();data_query=routepins(data_pin,C.addressof(data_routes),mem)
                    data_count=(data_routes[1]-data_routes[0])//8 if data_routes[0] else 0
                    if not 0<=data_count<=8:raise ValueError('Unexpected FF data route endpoint count')
                    route_points=[list(struct.unpack('<3h',C.string_at(ptr(address),6))) for address in range(data_routes[0],data_routes[1],8)] if data_count else []
                    records.append(dict(cell_type=cell_type,native_type=gettype(obj),slot=slot,ce=ce_label,control=reset_label,clock=clock_label,
                        native_pin_nodes={label:list(struct.unpack('<3h',C.string_at(ptr(pin+0x18),6))) if ptr(pin+0x18) else None
                            for label,pin in [('D',data_pin),('CLK',clock),('CE',ce),('SR',reset),('Q',getpin(obj,7))]},
                        native_ce_class=classify(encoder,ce),native_control_class=classify(encoder,reset),
                        native_clock_class=classify(encoder,clock),
                        paired_lut=paired,native_direct_connection=is_direct,wrong_slot_direct_connection=wrong_slot_direct,
                        native_data_route_query_return=route_return,native_data_route_nodes=node_count,
                        after_conversion_data_query=data_query,after_conversion_data_points=route_points,
                        after_conversion_lut_word=C.c_ushort.from_address(lut+0x20).value,after_conversion_lut_unused=C.c_ubyte.from_address(lut+0x22).value,
                        attributes=[list(struct.unpack('<19h',C.string_at(p+6,38))) for p in groups]))
            C.c_uint64.from_address(reg+8).value=0;C.c_ubyte.from_address(aggregate+0x318+slot).value=0
            C.c_ubyte.from_address(aggregate+0x326+slot).value=0;C.c_ubyte.from_address(lut+0x22).value=0
            if paired:C.c_uint64.from_address(lut+8).value=0
            C.c_uint64.from_address(data_pin+8).value=data_original
            C.c_uint64.from_address(ce+8).value=ce_original;C.c_uint64.from_address(reset+8).value=reset_original
            for p,value in original_flags.items():C.c_ubyte.from_address(p+0x4f).value=value
        print('CAPTURED',cell_type,flush=True)
    multiple=[]
    if multi:
        objects=[instances['dffre'],sentinel]
        dynamic_ce=ptr(getpin(objects[0],2)+8);dynamic_reset=ptr(getpin(objects[0],4)+8)
        pins=[(getpin(o,2),getpin(o,4),getpin(o,1)) for o in objects]
        for slots in ((0,2),(0,4),(2,4)):
            for obj,slot in zip(objects,slots):
                reg=ptr(aggregate+0xa0+8*slot);lut=ptr(aggregate+8*slot)
                setphy(obj,reg);C.c_uint64.from_address(reg+8).value=obj;bindpins(obj)
                C.c_ubyte.from_address(aggregate+0x318+slot).value=1;C.c_ubyte.from_address(aggregate+0x326+slot).value=1
                C.c_ubyte.from_address(lut+0x22).value=1
            labels=('zero','one','signal','inverted')
            profiles=list(itertools.product(labels,labels,('signal','inverted')))
            for profile_a,profile_b in itertools.product(profiles,repeat=2):
                for address,raw in zip(groups,defaults):C.memmove(address+6,raw,38)
                for (ce_pin,sr_pin,clk_pin),(ce_label,sr_label,clk_label) in zip(pins,(profile_a,profile_b)):
                    for pin,label,dynamic in ((ce_pin,ce_label,dynamic_ce),(sr_pin,sr_label,dynamic_reset)):
                        C.c_uint64.from_address(pin+8).value=dynamic if label in ('signal','inverted') else sources[label]
                    for pin,label in ((ce_pin,ce_label),(sr_pin,sr_label),(clk_pin,clk_label)):
                        C.c_ubyte.from_address(pin+0x4f).value=(C.c_ubyte.from_address(pin+0x4f).value&~0x40)|(0x40 if label=='inverted' else 0)
                convert(encoder,grid)
                multiple.append(dict(ffs=[dict(cell_type='DFFRE',slot=s,ce=p[0],control=p[1],clock=p[2]) for s,p in zip(slots,(profile_a,profile_b))],
                    attributes=[list(struct.unpack('<19h',C.string_at(p+6,38))) for p in groups]))
            for slot in slots:
                reg=ptr(aggregate+0xa0+8*slot);lut=ptr(aggregate+8*slot);C.c_uint64.from_address(reg+8).value=0
                C.c_ubyte.from_address(aggregate+0x318+slot).value=0;C.c_ubyte.from_address(aggregate+0x326+slot).value=0;C.c_ubyte.from_address(lut+0x22).value=0
        print('MULTI_FF_CASES',len(multiple),flush=True)
    shared_records=[]
    if shared:
        for cell_type,control_pin in [('DFFRE',4),('DFFSE',3),('DFFCE',6),('DFFPE',5)]:
            objects=[instances[cell_type.lower()],instances['shared_'+cell_type.lower()]]
            pins=[(getpin(o,2),getpin(o,control_pin),getpin(o,1)) for o in objects]
            originals={pin:(ptr(pin+8),C.c_ubyte.from_address(pin+0x4f).value) for ps in pins for pin in ps}
            dynamic_ce,dynamic_sr=originals[pins[0][0]][0],originals[pins[0][1]][0]
            data_pins=[getpin(o,0) for o in objects];data_originals=[ptr(p+8) for p in data_pins]
            shared_luts=[instances['shared_pair_a'],instances['shared_pair_b']] if shared_paired else []
            for slots in ((0,1),(2,3),(4,5)):
                for index,(obj,slot) in enumerate(zip(objects,slots)):
                    reg=ptr(aggregate+0xa0+8*slot);lut=ptr(aggregate+8*slot)
                    setphy(obj,reg);C.c_uint64.from_address(reg+8).value=obj;bindpins(obj)
                    C.c_ubyte.from_address(aggregate+0x318+slot).value=1;C.c_ubyte.from_address(aggregate+0x326+slot).value=1
                    C.c_ubyte.from_address(lut+0x22).value=0 if shared_paired else 1
                    if shared_paired:
                        source=shared_luts[index];setphy(source,lut);C.c_uint64.from_address(lut+8).value=source
                        C.c_ushort.from_address(lut+0x20).value=(0xAAAA,0xCCCC)[index]
                        C.c_uint64.from_address(data_pins[index]+8).value=ptr(getlutpin(source,8)+8)
                direct_checks=[]
                if shared_paired:
                    for index,(data_pin,slot) in enumerate(zip(data_pins,slots)):
                        vector=(C.c_uint64*3)();ok=routepins(data_pin,C.addressof(vector),mem)
                        good=direct(data_pin,mem);setphy(shared_luts[index],ptr(aggregate+8*slots[1-index]))
                        wrong=direct(data_pin,mem);setphy(shared_luts[index],ptr(aggregate+8*slot))
                        direct_checks.append(dict(slot=slot,direct=good,wrong_slot_direct=wrong,route_query_return=ok,
                            data_route_nodes=(vector[1]-vector[0])//8 if vector[0] else 0))
                for ce_label,sr_label,clk_label in itertools.product(('zero','one','signal','inverted'),('zero','one','signal','inverted'),('signal','inverted')):
                    for address,raw in zip(groups,defaults):C.memmove(address+6,raw,38)
                    for ce_pin,sr_pin,clk_pin in pins:
                        for pin,label,dynamic in ((ce_pin,ce_label,dynamic_ce),(sr_pin,sr_label,dynamic_sr)):
                            C.c_uint64.from_address(pin+8).value=dynamic if label in ('signal','inverted') else sources[label]
                        for pin,label in ((ce_pin,ce_label),(sr_pin,sr_label),(clk_pin,clk_label)):
                            C.c_ubyte.from_address(pin+0x4f).value=(originals[pin][1]&~0x40)|(0x40 if label=='inverted' else 0)
                    convert(encoder,grid)
                    nodes=[]
                    for obj in objects:
                        row={}
                        for label,index in (('D',0),('CLK',1),('CE',2),('SR',control_pin),('Q',7)):
                            pin=getpin(obj,index);node=ptr(pin+0x18)
                            row[label]=list(struct.unpack('<3h',C.string_at(node,6))) if node else None
                        nodes.append(row)
                    shared_records.append(dict(ffs=[dict(cell_type=cell_type,slot=s,ce=ce_label,control=sr_label,clock=clk_label) for s in slots],
                        native_pin_nodes=nodes,shared_control_nets_identical=all(ptr(pins[0][i]+8)==ptr(pins[1][i]+8) for i in range(3)),
                        data_nets_distinct=ptr(getpin(objects[0],0)+8)!=ptr(getpin(objects[1],0)+8),native_types=[gettype(o) for o in objects],
                        paired_luts=shared_paired,paired_data_checks=direct_checks,
                        attributes=[list(struct.unpack('<19h',C.string_at(p+6,38))) for p in groups]))
                for slot in slots:
                    reg=ptr(aggregate+0xa0+8*slot);lut=ptr(aggregate+8*slot);C.c_uint64.from_address(reg+8).value=0
                    C.c_ubyte.from_address(aggregate+0x318+slot).value=0;C.c_ubyte.from_address(aggregate+0x326+slot).value=0;C.c_ubyte.from_address(lut+0x22).value=0
                    if shared_paired:C.c_uint64.from_address(lut+8).value=0
                for pin,net in zip(data_pins,data_originals):C.c_uint64.from_address(pin+8).value=net
                for pin,(net,flag) in originals.items():C.c_uint64.from_address(pin+8).value=net;C.c_ubyte.from_address(pin+0x4f).value=flag
        print('SHARED_FF_CASES',len(shared_records),flush=True)
    (out/'ff_attributes.json').write_text(json.dumps(dict(site=[50,90],records=records,multi_records=multiple,shared_records=shared_records,defaults=[list(struct.unpack('<19h',raw)) for raw in defaults],
        scope='Native attribute queries only; shared cases use matching controls and separate data. No routed, paired-LUT data or configured-pair proof.' if shared else 'Native per-CFU attribute conversion; not shared-control legality or configured hardware proof.'),indent=2))


def fixture_netlist(shared=False,shared_paired=False):
    count=9 if shared else 5
    lines=[f'module top(clk,a,q); input clk; input [3:0] a; output [{count+(2 if shared_paired else 0)}:0] q;',f'wire ck,d,en,rs,one,zero,pair_net; wire [{count-1}:0] z;',
        'IBUF ibc(.I(clk),.O(ck)); IBUF ibd(.I(a[0]),.O(d)); IBUF ibe(.I(a[1]),.O(en)); IBUF ibr(.I(a[2]),.O(rs));',
        'VCC vc(.V(one)); GND gc(.G(zero)); GSR gs(.GSRI(one));']
    for i,(typ,pin) in enumerate([('DFFRE','RESET'),('DFFSE','SET'),('DFFCE','CLEAR'),('DFFPE','PRESET')]):
        lines.append(f'{typ} {typ.lower()}(.D(d),.CLK(ck),.CE(en),.{pin}(rs),.Q(z[{i}]));')
    lines.append('DFFRE sentinel(.D(d),.CLK(ck),.CE(one),.RESET(zero),.Q(z[4]));')
    if shared:
        lines.extend(['wire d_peer;','IBUF ib_shared_d(.I(a[3]),.O(d_peer));'])
        for i,(typ,pin) in enumerate([('DFFRE','RESET'),('DFFSE','SET'),('DFFCE','CLEAR'),('DFFPE','PRESET')],5):
            lines.append(f'{typ} shared_{typ.lower()}(.D(d_peer),.CLK(ck),.CE(en),.{pin}(rs),.Q(z[{i}]));')
    lines += [f'OBUF ob{i}(.I(z[{i}]),.O(q[{i}]));' for i in range(count)]
    lines += ["LUT4 pair_lut(.I0(d),.I1(en),.I2(rs),.I3(ck),.F(pair_net)); defparam pair_lut.INIT=16'h1234;",f'OBUF ob{count}(.I(pair_net),.O(q[{count}]));']
    if shared_paired:
        lines.extend(['wire shared_a_net,shared_b_net;',
            "LUT4 shared_pair_a(.I0(d),.I1(d_peer),.I2(en),.I3(rs),.F(shared_a_net)); defparam shared_pair_a.INIT=16'haaaa;",
            "LUT4 shared_pair_b(.I0(d),.I1(d_peer),.I2(en),.I3(rs),.F(shared_b_net)); defparam shared_pair_b.INIT=16'hcccc;",
            f'OBUF ob_shared_a(.I(shared_a_net),.O(q[{count+1}]));',f'OBUF ob_shared_b(.I(shared_b_net),.O(q[{count+2}]));'])
    lines.append('endmodule')
    return '\n'.join(lines)+'\n'


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--out',type=Path,required=True);ap.add_argument('--worker',action='store_true');ap.add_argument('--paired',action='store_true');ap.add_argument('--multi',action='store_true');ap.add_argument('--shared',action='store_true');ap.add_argument('--shared-paired',action='store_true');a=ap.parse_args();out=a.out.resolve()
    if a.shared_paired:a.shared=True
    if a.shared and (a.paired or a.multi):ap.error('Shared-control calibration is a separate standalone fixture')
    if a.worker:return worker(out,a.paired,a.multi,a.shared,a.shared_paired)
    from workspace_budget import reserve
    reserve(ROOT,50_000_000)
    prepare(out)
    (out/'probe.vg').write_text(fixture_netlist(a.shared,a.shared_paired));shutil.copyfile(__file__,out/'extractor_source.py.txt')
    python=ROOT/'binary_audit/flow/runtime/bin/python.exe';start=time.monotonic();rc=None;timeout=False
    with (out/'worker.log').open('w') as log:
        try:rc=subprocess.run([str(python),str(Path(__file__).resolve()),'--worker','--out',str(out)]+(['--paired'] if a.paired else [])+(['--multi'] if a.multi else [])+(['--shared'] if a.shared else [])+(['--shared-paired'] if a.shared_paired else []),cwd=python.parent,
            env=dict(os.environ,PYTHONHOME=sys.base_prefix),stdout=log,stderr=subprocess.STDOUT,timeout=120).returncode
        except subprocess.TimeoutExpired:timeout=True
    result=dict(status='captured' if rc==0 and (out/'ff_attributes.json').exists() else 'failed',returncode=rc,timeout=timeout,wall_seconds=time.monotonic()-start,
        dll_sha256=EXPECTED,netlist_sha256=sha(out/'probe.vg'),open_chip_database_used=False,vendor_placement_executed=False,vendor_routing_executed=False,device_programmed=False)
    if result['status']=='captured':result['attributes_sha256']=sha(out/'ff_attributes.json')
    from extract_native_fse import Fse
    result['source_fse_sha256']=Fse().sha256
    (out/'manifest.json').write_text(json.dumps(result,indent=2));print(json.dumps(result,indent=2))
    if result['status']=='failed':raise SystemExit(1)


if __name__=='__main__':main()
