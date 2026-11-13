"""Isolated DFFPE query adapter; promotion to the integrated model is separate."""
import ctypes as C
import math,struct

def capture_one(native,obj,name):
    fn=native.fn;ptr=native.ptr;vp=C.c_void_p
    phy=fn('?getPhyInst@Instance@NETLIST@@QEAAPEAVinstance@2@XZ',vp,[vp])(obj)
    site=list(struct.unpack('<4h',C.string_at(phy+0x10,8)))
    if site[0]!=0 or site[3]!=1:raise ValueError('PRESET query requires output I/O register')
    pin=fn('?getOnePin@Reg@NETLIST@@UEAAPEAVPin@2@I@Z',vp,[vp,C.c_uint]);clock=pin(obj,1)
    monitor=fn('?getTmMonitor@globalMem@@QEAAPEAVTmMonitor@STA@@XZ',vp,[vp])(native.mem);library=ptr(ptr(monitor+8)+0x38)
    query=C.CFUNCTYPE(None,vp,vp,vp,vp,C.c_int,C.c_bool,C.c_bool)(native.dll._handle+0x1446fc0);rows=[]
    for label,index in [('D',0),('CE',2),('PRESET',5),('Q',7)]:
        target=pin(obj,index)
        if not target:raise ValueError('Missing DFFPE timing pin')
        for selector in range(3):
            for maximum in ((True,) if label=='Q' else (True,False)):
                values=(C.c_float*4)(*[float('nan')]*4);query(library,C.addressof(values),clock,target,selector,maximum,True)
                if not all(math.isfinite(v) for v in values):raise ValueError('Incomplete DFFPE timing query')
                rows.append(dict(pin=label,selector=selector,maximum=maximum,values=list(values)))
    return dict(instance=name,site=site,clock_positive=True,queries=rows)
