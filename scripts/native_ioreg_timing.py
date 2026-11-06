"""Native FF dispatcher queries for positive-edge output I/O registers."""
import ctypes as C
import json,math,struct


def capture_one(native,obj,name):
    fn=native.fn;ptr=native.ptr;vp=C.c_void_p
    phy=fn('?getPhyInst@Instance@NETLIST@@QEAAPEAVinstance@2@XZ',vp,[vp])(obj)
    site=list(struct.unpack('<4h',C.string_at(phy+0x10,8)))
    if site[0]!=0 or site[3]!=1:raise ValueError('FF query requires an output I/O register')
    pin=fn('?getOnePin@Reg@NETLIST@@UEAAPEAVPin@2@I@Z',vp,[vp,C.c_uint]);clock=pin(obj,1)
    monitor=fn('?getTmMonitor@globalMem@@QEAAPEAVTmMonitor@STA@@XZ',vp,[vp])(native.mem);library=ptr(ptr(monitor+8)+0x38)
    query=C.CFUNCTYPE(None,vp,vp,vp,vp,C.c_int,C.c_bool,C.c_bool)(native.dll._handle+0x1446fc0);rows=[]
    controls=[(label,index) for label,index in [('PRESET',5),('CLEAR',6)] if pin(obj,index) and ptr(pin(obj,index)+8)]
    if len(controls)!=1:raise ValueError('Output I/O timing requires one connected asynchronous control')
    for label,index in [('D',0),('CE',2),controls[0],('Q',7)]:
        target=pin(obj,index)
        if not target:raise ValueError('Missing output I/O timing pin')
        for selector in range(3):
            for maximum in ((True,) if label=='Q' else (True,False)):
                values=(C.c_float*4)(*[float('nan')]*4);query(library,C.addressof(values),clock,target,selector,maximum,True)
                if not all(math.isfinite(v) for v in values):raise ValueError('Incomplete output-register timing query')
                rows.append(dict(pin=label,selector=selector,maximum=maximum,values=list(values)))
    return dict(instance=name,site=site,clock_positive=True,queries=rows)


def capture(native,out):
    fn=native.fn;ptr=native.ptr;vp=C.c_void_p;v=(C.c_uint64*3)()
    design=fn('?getDesign@globalMem@@QEAAPEAVDesign@NETLIST@@XZ',vp,[vp])(native.mem)
    fn('?getHierAllInsts@Design@NETLIST@@QEAAXAEAV?$vector@PEAVInstance@NETLIST@@V?$allocator@PEAVInstance@NETLIST@@@std@@@std@@@Z',None,[vp,vp])(design,C.addressof(v))
    gettype=fn('?getType@Instance@NETLIST@@QEAA?AW4INS_TYPE@2@XZ',C.c_int,[vp]);getphy=fn('?getPhyInst@Instance@NETLIST@@QEAAPEAVinstance@2@XZ',vp,[vp]);rows=[]
    for a in range(v[0],v[1],8):
        obj=ptr(a)
        if gettype(obj)!=50:continue
        phy=getphy(obj)
        if not phy or C.c_short.from_address(phy+0x10).value!=0:continue
        b=C.create_string_buffer(32);fn('?getName@Instance@NETLIST@@QEAA?AV?$basic_string@DU?$char_traits@D@std@@V?$allocator@D@2@@std@@XZ',vp,[vp,vp])(obj,C.addressof(b));p=C.addressof(b);n=ptr(p+16)
        if n>4096:raise ValueError('Invalid native register name')
        name=C.string_at(ptr(p) if ptr(p+24)>=16 else p,n).decode();rows.append(capture_one(native,obj,name))
    if not rows:raise ValueError('Empty output-register timing capture')
    result=dict(registers=rows,scope='Native dispatcher observations; independent raw library and report audit required')
    (out/'ioreg_timing_queries.json').write_text(json.dumps(result,indent=2));return result
