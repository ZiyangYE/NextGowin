"""Read native asynchronous FF and dedicated carry timing queries.

Run only after a real final STA evaluation, in the isolated native process.
These observations are not by themselves a model or physical legality proof.
"""
import ctypes as C
import json,math,struct


def capture(native,out):
    fn=native.fn;ptr=native.ptr;vp=C.c_void_p
    design=fn('?getDesign@globalMem@@QEAAPEAVDesign@NETLIST@@XZ',vp,[vp])(native.mem)
    vec=(C.c_uint64*3)();fn('?getHierAllInsts@Design@NETLIST@@QEAAXAEAV?$vector@PEAVInstance@NETLIST@@V?$allocator@PEAVInstance@NETLIST@@@std@@@std@@@Z',None,[vp,vp])(design,C.addressof(vec))
    monitor=fn('?getTmMonitor@globalMem@@QEAAPEAVTmMonitor@STA@@XZ',vp,[vp])(native.mem);library=ptr(ptr(monitor+8)+0x38)
    gettype=fn('?getType@Instance@NETLIST@@QEAA?AW4INS_TYPE@2@XZ',C.c_int,[vp]);getphy=fn('?getPhyInst@Instance@NETLIST@@QEAAPEAVinstance@2@XZ',vp,[vp])
    namefn=fn('?getName@Instance@NETLIST@@QEAA?AV?$basic_string@DU?$char_traits@D@std@@V?$allocator@D@2@@std@@XZ',vp,[vp,vp])
    report=fn('?getPinNameForRpt@Pin@NETLIST@@QEAAXAEAV?$basic_string@DU?$char_traits@D@std@@V?$allocator@D@2@@std@@@Z',None,[vp,vp])
    def string(buf):
        p=C.addressof(buf);n=ptr(p+16)
        if n>4096:raise ValueError('Invalid timing query name')
        return C.string_at(ptr(p) if ptr(p+24)>=16 else p,n).decode()
    def label(pin):
        buf=C.create_string_buffer(32);C.c_uint64.from_address(C.addressof(buf)+24).value=15;report(pin,C.addressof(buf));return string(buf).split('.',1)[1]
    ffquery=C.CFUNCTYPE(None,vp,vp,vp,vp,C.c_int,C.c_bool,C.c_bool)(native.dll._handle+0x1446fc0)
    aluquery=C.CFUNCTYPE(None,vp,vp,vp,vp,C.c_int)(native.dll._handle+0x14444f0)
    netquery=fn('?getOneNetSinkDly@TmMonitor@STA@@QEAAMPEAVPin@NETLIST@@0_N@Z',C.c_float,[vp,vp,vp,C.c_bool])
    edgequery=C.CFUNCTYPE(vp,vp,vp,vp)(native.dll._handle+0x1391ba0)
    pr=ptr(native.mem+0x18)
    if not pr:raise ValueError('Dedicated carry query requires initialized native context')
    p2r=fn('?getP2R@PlaceRoute@PLACEROUTE@@QEAAPEAVP2R@2@XZ',vp,[vp])(pr)
    # Read-only lookup of the P2R Net* -> direct-carry flag map, as inspected
    # in Pin::isDirectConn 0x2f016b..0x2f01ad. No ownership is fabricated.
    head=ptr(p2r+0x10);root=ptr(head+8);ownership={};stack=[root];visited=set()
    while stack:
        node=stack.pop()
        if C.c_ubyte.from_address(node+0x19).value:continue
        if node in visited or len(visited)>1000000:raise ValueError('Invalid carry ownership tree')
        visited.add(node);ownership[ptr(node+0x20)]=C.c_ubyte.from_address(node+0x28).value;stack.extend((ptr(node),ptr(node+0x10)))
    ff=[];carry=[];alu=[]
    for a in range(vec[0],vec[1],8):
        obj=ptr(a);typ=gettype(obj)
        if typ not in (50,106):continue
        buf=C.create_string_buffer(32);namefn(obj,C.addressof(buf));name=string(buf);phy=getphy(obj)
        if not phy:raise ValueError('Unplaced timing query primitive')
        cls='Reg' if typ==50 else 'Alu';pins={}
        for direction in ('In','Out'):
            buf=(C.c_uint64*3)();fn(f'?getAll{direction}Pins@{cls}@NETLIST@@UEAA?AV?$vector@PEAVPin@NETLIST@@V?$allocator@PEAVPin@NETLIST@@@std@@@std@@XZ',vp,[vp,vp])(obj,C.addressof(buf))
            for p in range(buf[0],buf[1],8):pin=ptr(p);pins[label(pin)]=pin
        site=list(struct.unpack('<4h',C.string_at(phy+0x10,8)))
        if typ==50:
            for control in ('CLEAR','PRESET'):
                if control not in pins:continue
                values=[]
                for selector in range(3):
                    for maximum in (True,False):
                        data=(C.c_float*4)(*[float('nan')]*4);ffquery(library,C.addressof(data),pins['CLK'],pins[control],selector,maximum,True)
                        if not all(math.isfinite(v) for v in data):raise ValueError('Incomplete asynchronous FF query')
                        values.append(dict(selector=selector,analysis='Recovery' if maximum else 'Removal',values=list(data)))
                ff.append(dict(instance=name,control=control,site=site,pin_id=C.c_int.from_address(pins[control]+0x48).value,clock_positive=True,queries=values))
        else:
            queries=[];pin_nodes={}
            for pin,pointer in pins.items():
                node=ptr(pointer+0x18);pin_nodes[pin]=list(struct.unpack('<3h',C.string_at(node,6))) if node else None
            for source_pin in ('I0','I1','I3','CIN'):
                for destination_pin in ('SUM','COUT'):
                    for selector in range(3):
                        data=(C.c_float*4)(*[float('nan')]*4);aluquery(library,C.addressof(data),pins[source_pin],pins[destination_pin],selector)
                        if not all(math.isfinite(v) for v in data):raise ValueError('Incomplete ALU cell query')
                        queries.append(dict(source=source_pin,destination=destination_pin,selector=selector,values=list(data)))
            buf=C.create_string_buffer(32);fn('?getInitValue@Alu@NETLIST@@QEAA?AV?$basic_string@DU?$char_traits@D@std@@V?$allocator@D@2@@std@@XZ',vp,[vp,vp])(obj,C.addressof(buf))
            alu.append(dict(instance=name,mode=int(string(buf)),site=site,pin_nodes=pin_nodes,queries=queries))
            sink=pins['CIN'];net=ptr(sink+8);source=ptr(net+0x18) if net else 0
            if not source:continue
            source_obj=ptr(source+0x10)
            if gettype(source_obj)!=106 or label(source)!='COUT':continue
            source_buf=C.create_string_buffer(32);namefn(source_obj,C.addressof(source_buf));source_name=string(source_buf);source_phy=getphy(source_obj)
            if not ptr(source+0x20) or not ptr(sink+0x20):raise ValueError('Missing carry timing nodes')
            edge=edgequery(ptr(monitor+8),ptr(source+0x20),ptr(sink+0x20))
            if not edge:raise ValueError('Missing carry STA edge; zero returned by fallback is not a delay measurement')
            maximum=netquery(monitor,source,sink,True);minimum=netquery(monitor,source,sink,False)
            if not all(math.isfinite(v) for v in (maximum,minimum)):raise ValueError('Invalid carry delay')
            carry.append(dict(source=source_name,destination=name,source_site=list(struct.unpack('<4h',C.string_at(source_phy+0x10,8))),destination_site=site,
                maximum_ns=maximum,minimum_ns=minimum,ownership_flag=ownership.get(net),source_timing_node=True,destination_timing_node=True,timing_edge_present=True))
    groupid=C.c_short.from_address(ptr(library+0x10)+2*2).value;group=ptr(ptr(library+8)+8*groupid);rows={}
    for rowid in (27,28,29,30):
        row=C.c_short.from_address(ptr(group+8)+rowid*2).value;p=ptr(ptr(group)+row*8)
        if C.c_short.from_address(p).value!=12:raise ValueError('Unknown FF row layout')
        rows[str(rowid)]=list(struct.unpack('<12f',C.string_at(ptr(p+8),48)))
    result=dict(async_ff=ff,alu_cells=alu,carry_links=carry,async_rows=rows,carry_ownership_entries=len(ownership),
        scope='Native FF dispatcher and pin-to-pin carry query observations only; not full timing/physical signoff')
    (out/'extended_timing_queries.json').write_text(json.dumps(result,indent=2));return result
