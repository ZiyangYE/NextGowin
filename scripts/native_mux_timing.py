"""Observe dedicated mux cell/edge timing in a completed native reference."""
import ctypes as C
import json,math,struct


def capture(native,out):
    fn=native.fn;ptr=native.ptr;vp=C.c_void_p
    design=fn('?getDesign@globalMem@@QEAAPEAVDesign@NETLIST@@XZ',vp,[vp])(native.mem);monitor=fn('?getTmMonitor@globalMem@@QEAAPEAVTmMonitor@STA@@XZ',vp,[vp])(native.mem);context=ptr(monitor+8);library=ptr(context+0x38)
    vec=(C.c_uint64*3)();fn('?getHierAllInsts@Design@NETLIST@@QEAAXAEAV?$vector@PEAVInstance@NETLIST@@V?$allocator@PEAVInstance@NETLIST@@@std@@@std@@@Z',None,[vp,vp])(design,C.addressof(vec))
    namefn=fn('?getName@Instance@NETLIST@@QEAA?AV?$basic_string@DU?$char_traits@D@std@@V?$allocator@D@2@@std@@XZ',vp,[vp,vp]);gettype=fn('?getType@Instance@NETLIST@@QEAA?AW4INS_TYPE@2@XZ',C.c_int,[vp]);getphy=fn('?getPhyInst@Instance@NETLIST@@QEAAPEAVinstance@2@XZ',vp,[vp])
    report=fn('?getPinNameForRpt@Pin@NETLIST@@QEAAXAEAV?$basic_string@DU?$char_traits@D@std@@V?$allocator@D@2@@std@@@Z',None,[vp,vp])
    # GW5A/5AT dispatcher, not the older-family 0x144af70 implementation.
    query=C.CFUNCTYPE(None,vp,vp,vp,vp,C.c_int,C.c_int)(native.dll._handle+0x1462550)
    edgequery=C.CFUNCTYPE(vp,vp,vp,vp)(native.dll._handle+0x1391ba0)
    netquery=fn('?getOneNetSinkDly@TmMonitor@STA@@QEAAMPEAVPin@NETLIST@@0_N@Z',C.c_float,[vp,vp,vp,C.c_bool])
    def string(buf):
        p=C.addressof(buf);size=ptr(p+16)
        if size>4096:raise ValueError('Invalid mux timing name')
        return C.string_at(ptr(p) if ptr(p+24)>=16 else p,size).decode()
    rows=[]
    for a in range(vec[0],vec[1],8):
        obj=ptr(a)
        if gettype(obj)!=2:continue
        vt=ptr(obj);loc=ptr(vt-8);type_rva=C.c_uint32.from_address(loc+12).value
        if C.string_at(native.dll._handle+type_rva+16)!=b'.?AVMux2@NETLIST@@':continue
        typ=fn('?getSubType@Mux2@NETLIST@@UEAA?AW4INS_TYPE@2@XZ',C.c_int,[vp])(obj)
        if typ not in (33,34,35):continue
        phy=getphy(obj)
        if not phy:raise ValueError('Unplaced mux timing reference')
        buf=C.create_string_buffer(32);namefn(obj,C.addressof(buf));name=string(buf);pins=(C.c_uint64*3)()
        fn('?getAllInPins@Mux2@NETLIST@@UEAA?AV?$vector@PEAVPin@NETLIST@@V?$allocator@PEAVPin@NETLIST@@@std@@@std@@XZ',vp,[vp,vp])(obj,C.addressof(pins));queries=[];links=[]
        for p in range(pins[0],pins[1],8):
            pin=ptr(p);buf=C.create_string_buffer(32);C.c_uint64.from_address(C.addressof(buf)+24).value=15;report(pin,C.addressof(buf));label=string(buf).split('.',1)[1]
            for selector in range(3):
                values=(C.c_float*4)(*[float('nan')]*4);query(library,C.addressof(values),obj,pin,selector,70)
                if not all(math.isfinite(v) for v in values):raise ValueError('Incomplete native dedicated mux timing query')
                queries.append(dict(pin=label,selector=selector,values=list(values)))
            if label in ('I0','I1'):
                net=ptr(pin+8);source=ptr(net+0x18) if net else 0;edge=edgequery(context,ptr(source+0x20),ptr(pin+0x20)) if source and ptr(source+0x20) and ptr(pin+0x20) else 0
                source_obj=ptr(source+0x10) if source else 0;source_phy=getphy(source_obj) if source_obj else 0;buf=C.create_string_buffer(32)
                if source_obj:namefn(source_obj,C.addressof(buf))
                links.append(dict(pin=label,source=string(buf) if source_obj else None,source_site=list(struct.unpack('<4h',C.string_at(source_phy+0x10,8))) if source_phy else None,
                    timing_edge_present=bool(edge),maximum_ns=netquery(monitor,source,pin,True) if edge else None,minimum_ns=netquery(monitor,source,pin,False) if edge else None))
        rows.append(dict(instance=name,level=typ-28,site=list(struct.unpack('<4h',C.string_at(phy+0x10,8))),queries=queries,links=links))
    if not rows:raise ValueError('Empty dedicated mux timing capture cannot establish coverage')
    result=dict(muxes=rows,scope='Native dedicated mux timing queries and actual internal STA edges; geometry, encoding and broad mode legality are separate checks.')
    (out/'mux_timing_queries.json').write_text(json.dumps(result,indent=2));return result
