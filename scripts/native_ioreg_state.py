"""Read logical/physical register and I/O-logic bindings after native P&R.

This is a calibration inventory, not an own placement or legality claim.
"""
import ctypes as C
import struct


def capture(native):
    fn=native.fn;ptr=native.ptr;vp=C.c_void_p
    def classname(obj):
        if not obj:return None
        vt=ptr(obj)
        if not native.dll._handle<=vt<native.dll._handle+0x2000000:return None
        locator=ptr(vt-8);type_rva=C.c_uint32.from_address(locator+12).value
        return C.string_at(native.dll._handle+type_rva+16).decode('ascii')
    def string(p):
        n=ptr(p+16)
        if n>4096:raise ValueError('Invalid native I/O-register label')
        return C.string_at(ptr(p) if ptr(p+24)>=16 else p,n).decode()
    getname=fn('?getName@Instance@NETLIST@@QEAA?AV?$basic_string@DU?$char_traits@D@std@@V?$allocator@D@2@@std@@XZ',vp,[vp,vp])
    def name(obj):
        if not obj:return None
        b=C.create_string_buffer(32);getname(obj,C.addressof(b));return string(C.addressof(b))
    getphy=fn('?getPhyInst@Instance@NETLIST@@QEAAPEAVinstance@2@XZ',vp,[vp])
    gettype=fn('?getType@Instance@NETLIST@@QEAA?AW4INS_TYPE@2@XZ',C.c_int,[vp])
    def header(p):return list(struct.unpack('<4h',C.string_at(p+0x10,8))) if p else None
    design=fn('?getDesign@globalMem@@QEAAPEAVDesign@NETLIST@@XZ',vp,[vp])(native.mem);vector=(C.c_uint64*3)()
    fn('?getHierAllInsts@Design@NETLIST@@QEAAXAEAV?$vector@PEAVInstance@NETLIST@@V?$allocator@PEAVInstance@NETLIST@@@std@@@std@@@Z',None,[vp,vp])(design,C.addressof(vector))
    def items(v):
        a,b,end=v
        if not a<=b<=end or (b-a)%8 or (b-a)//8>1000000:raise ValueError('Invalid I/O-register inventory vector')
        return [ptr(x) for x in range(a,b,8)]
    rows=[];inventory={}
    physical_registers={}
    for obj in items(vector):
        cls=classname(obj);inventory[cls]=inventory.get(cls,0)+1
        if cls not in ('.?AVReg@NETLIST@@','.?AVIologic@NETLIST@@'):continue
        kind='Reg' if cls=='.?AVReg@NETLIST@@' else 'Iologic';phy=getphy(obj)
        row=dict(instance=name(obj),logical_class=cls,logical_type=gettype(obj),
            subtype=fn(f'?getSubType@{kind}@NETLIST@@UEAA?AW4INS_TYPE@2@XZ',C.c_int,[vp])(obj),
            physical_class=classname(phy),physical_header=header(phy),physical_base_logic_matches=bool(phy and ptr(phy+8)==obj),pins=[])
        if kind=='Reg':
            if phy:physical_registers[phy]=row
            row['flags']={k:fn(f'?get{k}@Reg@NETLIST@@QEAAEXZ',C.c_ubyte,[vp])(obj) for k in ('BindFlag','SpecFlag')}
            typed=fn('?getPhyReg@Reg@NETLIST@@QEAAPEAVreg@2@XZ',vp,[vp])(obj)
            row.update(typed_physical_class=classname(typed),typed_physical_header=header(typed),typed_and_base_match=typed==phy)
            connected=(C.c_uint64*3)();fn('?getAllConIobufs@Reg@NETLIST@@UEAA?AV?$vector@PEAVIobuf@NETLIST@@V?$allocator@PEAVIobuf@NETLIST@@@std@@@std@@XZ',vp,[vp,vp])(obj,C.addressof(connected))
            row['connected_iobufs']=[dict(instance=name(io),site=header(getphy(io))) for io in items(connected)]
        for direction in ('In','Out'):
            pins=(C.c_uint64*3)();fn(f'?getAll{direction}Pins@{kind}@NETLIST@@UEAA?AV?$vector@PEAVPin@NETLIST@@V?$allocator@PEAVPin@NETLIST@@@std@@@std@@XZ',vp,[vp,vp])(obj,C.addressof(pins))
            for pin in items(pins):
                label=C.create_string_buffer(32);C.c_uint64.from_address(C.addressof(label)+24).value=15
                fn('?getPinNameForRpt@Pin@NETLIST@@QEAAXAEAV?$basic_string@DU?$char_traits@D@std@@V?$allocator@D@2@@std@@@Z',None,[vp,vp])(pin,C.addressof(label))
                net=ptr(pin+8);driver=ptr(net+0x18) if net else 0;source=ptr(driver+0x10) if driver else 0;raw=ptr(pin+0x18);nodes=(C.c_uint64*3)()
                ok=fn('?getNodebyPinForSta@Pin@NETLIST@@QEAA_NAEAV?$vector@PEAVnode@NETLIST@@V?$allocator@PEAVnode@NETLIST@@@std@@@std@@PEAVglobalMem@@@Z',C.c_bool,[vp,vp,vp])(pin,C.addressof(nodes),native.mem)
                row['pins'].append(dict(pin=string(C.addressof(label)),direction=direction,native_type=C.c_int.from_address(pin+0x48).value,
                    native_flags=C.c_ubyte.from_address(pin+0x4f).value,
                    raw_node=list(struct.unpack('<3h',C.string_at(raw,6))) if raw else None,sta_query_return=ok,
                    sta_nodes=[list(struct.unpack('<3h',C.string_at(n,6))) for n in items(nodes)],source=name(source),source_site=header(getphy(source)) if source else None))
        rows.append(row)
    io_vector=fn('?getIobufVecReference@Design@NETLIST@@QEAAAEAV?$vector@PEAVIobuf@NETLIST@@V?$allocator@PEAVIobuf@NETLIST@@@std@@@std@@XZ',vp,[vp])(design)
    getgrid=fn('?getGridByRowCol@chip@NETLIST@@QEAAPEAVgrid@2@FF@Z',vp,[vp,C.c_short,C.c_short])
    getblk=fn('?get_blk@iob@NETLIST@@QEAAPEAVioblk@2@F@Z',vp,[vp,C.c_short]);blocks=[];direct_edges=[]
    monitor=fn('?getTmMonitor@globalMem@@QEAAPEAVTmMonitor@STA@@XZ',vp,[vp])(native.mem)
    edge_query=C.CFUNCTYPE(vp,vp,vp,vp)(native.dll._handle+0x1391ba0)
    net_query=fn('?getOneNetSinkDly@TmMonitor@STA@@QEAAMPEAVPin@NETLIST@@0_N@Z',C.c_float,[vp,vp,vp,C.c_bool])
    for obj in items((ptr(io_vector),ptr(io_vector+8),ptr(io_vector+16))):
        phy=getphy(obj);_,r,c,slot=header(phy);block=getblk(getgrid(native.chip,r,c),slot)
        if classname(block)!='.?AVioblk5AT@NETLIST@@':continue
        record=dict(iobuf=name(obj),site=[r,c,slot],block_class=classname(block),direct_reg_refs=[],iologic=[],vector_58=[])
        pins=(C.c_uint64*3)();fn('?getAllInPins@Iobuf@NETLIST@@UEAA?AV?$vector@PEAVPin@NETLIST@@V?$allocator@PEAVPin@NETLIST@@@std@@@std@@XZ',vp,[vp,vp])(obj,C.addressof(pins))
        for pin in items(pins):
            net=ptr(pin+8);source=ptr(net+0x18) if net else 0;source_obj=ptr(source+0x10) if source else 0
            if not source_obj or classname(source_obj)!='.?AVReg@NETLIST@@':continue
            source_phy=getphy(source_obj)
            if not source_phy or header(source_phy)[0]!=0:continue
            nodes=(C.c_uint64*3)();ok=fn('?getNodebyPinForSta@Pin@NETLIST@@QEAA_NAEAV?$vector@PEAVnode@NETLIST@@V?$allocator@PEAVnode@NETLIST@@@std@@@std@@PEAVglobalMem@@@Z',C.c_bool,[vp,vp,vp])(pin,C.addressof(nodes),native.mem)
            edge=edge_query(ptr(monitor+8),ptr(source+0x20),ptr(pin+0x20)) if ptr(source+0x20) and ptr(pin+0x20) else 0
            direct_edges.append(dict(source=name(source_obj),destination=name(obj),source_site=header(source_phy),destination_site=header(phy),
                source_has_timing_node=bool(ptr(source+0x20)),destination_has_timing_node=bool(ptr(pin+0x20)),timing_edge_present=bool(edge),
                minimum_ns=net_query(monitor,source,pin,False) if edge else None,maximum_ns=net_query(monitor,source,pin,True) if edge else None,
                generic_direct_predicate=bool(fn('?isDirectConn@Pin@NETLIST@@QEAA_NPEAVglobalMem@@@Z',C.c_bool,[vp,vp])(pin,native.mem)),
                sta_query_return=ok,sta_nodes=[list(struct.unpack('<3h',C.string_at(n,6))) for n in items(nodes)]))
        for offset in range(0x10,0xc0,8):
            target=ptr(block+offset)
            if target in physical_registers:record['direct_reg_refs'].append(dict(offset=offset,instance=physical_registers[target]['instance']))
        for offset in (0x28,0x30,0x38,0x40,0x78,0x80):
            objphy=ptr(block+offset)
            if not objphy:continue
            if classname(objphy)!='.?AViologic@NETLIST@@':raise ValueError('Unexpected physical I/O-logic class')
            refs=[dict(offset=i,instance=physical_registers[ptr(objphy+i)]['instance']) for i in range(0x18,0x998,8) if ptr(objphy+i) in physical_registers]
            record['iologic'].append(dict(block_offset=offset,header=header(objphy),reg_refs=refs,
                attributes=list(struct.unpack('<171h',C.string_at(objphy+0x840,342)))))
        for reg in items((ptr(block+0x58),ptr(block+0x60),ptr(block+0x68))):
            record['vector_58'].append(dict(class_name=classname(reg),header=header(reg),instance=physical_registers.get(reg,{}).get('instance')))
        blocks.append(record)
    return dict(logical_classes=inventory,registers_and_iologic=rows,iob_blocks=blocks,output_register_edges=direct_edges,read_only=True,own_pnr_result=False,
        scope='Native calibration bindings and STA endpoints; no configuration or generalized I/O-register legality certified.')
