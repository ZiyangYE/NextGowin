"""Read scalar physical/logical I/O attributes for the hash-pinned adapter."""
import ctypes as C
import struct


def capture(native):
    fn=native.fn;ptr=native.ptr;vp=C.c_void_p
    design=fn('?getDesign@globalMem@@QEAAPEAVDesign@NETLIST@@XZ',vp,[vp])(native.mem)
    vector=fn('?getIobufVecReference@Design@NETLIST@@QEAAAEAV?$vector@PEAVIobuf@NETLIST@@V?$allocator@PEAVIobuf@NETLIST@@@std@@@std@@XZ',vp,[vp])(design)
    namefn=fn('?getName@Instance@NETLIST@@QEAA?AV?$basic_string@DU?$char_traits@D@std@@V?$allocator@D@2@@std@@XZ',vp,[vp,vp])
    getphy=fn('?getPhyInst@Instance@NETLIST@@QEAAPEAVinstance@2@XZ',vp,[vp])
    logical=fn('?getPioValueByAttr@Iobuf@NETLIST@@QEAA?AW4PIO_VALUE@2@W4PIO_ATTR@2@@Z',C.c_int,[vp,C.c_int])
    physical=fn('?getPioAttr@iobuf@NETLIST@@QEAAFW4PIO_ATTR@2@@Z',C.c_short,[vp,C.c_int])
    def enum_string(symbol,value):
        buf=C.create_string_buffer(32);fn(symbol,vp,[vp,C.c_int])(C.addressof(buf),value);p=C.addressof(buf);n=ptr(p+16)
        if n>4096:raise ValueError('Invalid enum name')
        return C.string_at(ptr(p) if ptr(p+24)>=16 else p,n).decode()
    attr_symbol='?getStringfromAttr@NETLIST@@YA?AV?$basic_string@DU?$char_traits@D@std@@V?$allocator@D@2@@std@@W4PIO_ATTR@1@@Z'
    value_symbol='?getStringfromAttrVal@NETLIST@@YA?AV?$basic_string@DU?$char_traits@D@std@@V?$allocator@D@2@@std@@W4PIO_VALUE@1@@Z'
    rows=[]
    for a in range(ptr(vector),ptr(vector+8),8):
        obj=ptr(a);buf=C.create_string_buffer(32);namefn(obj,C.addressof(buf));p=C.addressof(buf);n=ptr(p+16)
        if n>4096:raise ValueError('Invalid I/O name')
        name=C.string_at(ptr(p) if ptr(p+24)>=16 else p,n).decode();phy=getphy(obj)
        rows.append(dict(instance=name,site=list(struct.unpack('<4h',C.string_at(phy+0x10,8))) if phy else None,
            logical=[logical(obj,i) for i in range(16)],physical=[physical(phy,i) for i in range(16)] if phy else None))
    gsr=fn('?getGsr@Design@NETLIST@@QEAAPEAVGsr@2@XZ',vp,[vp])(design)
    phy=fn('?getGsr@chip@NETLIST@@QEAAPEAVgsr@2@XZ',vp,[vp])(native.chip)
    return dict(io=rows,attributes={str(i):enum_string(attr_symbol,i) for i in range(16)},
        values={str(i):enum_string(value_symbol,i) for i in range(129)},gsr=dict(logical_present=bool(gsr),physical_present=bool(phy),
        logical_bound=bool(getphy(gsr)) if gsr else False,physical_has_logic=bool(ptr(phy+8)) if phy else False,
        physical_cached_logic=bool(ptr(phy+0x20)) if phy else False))
