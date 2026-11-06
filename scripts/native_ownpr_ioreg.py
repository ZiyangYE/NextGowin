"""Bind independently selected fixed-pad FFs; no vendor placement/routing."""
import ctypes as C
import struct
from ownpr_ioreg_macros import validate


def bind_ioregs(native,design,placed,mapping,bind):
    check=validate(design,placed);fn=native.fn;ptr=native.ptr;vp=C.c_void_p;rows=[]
    getblk=fn('?get_blk@iob@NETLIST@@QEAAPEAVioblk@2@F@Z',vp,[vp,C.c_short]);pinfn=fn('?getOnePin@Reg@NETLIST@@UEAAPEAVPin@2@I@Z',vp,[vp,C.c_uint])
    for record in check['records']:
        r,c,slot=record['site'];block=getblk(native.getgrid(native.chip,r,c),slot);io=ptr(block+0x28);reg=ptr(io+0x818)
        if list(struct.unpack('<4h',C.string_at(reg+0x10,8)))!=[0,r,c,1] or ptr(reg+8):raise ValueError('Output register physical slot invalid/occupied')
        obj=bind(record['instance'],reg);fn('?setPin2Node@Reg@NETLIST@@UEAAXXZ',None,[vp])(obj)
        actual={}
        control=record['control_pin']
        for label,index in [('D',0),('CLK',1),('CE',2),(control,5 if control=='PRESET' else 6),('Q',7)]:
            pin=pinfn(obj,index)
            if not pin:raise ValueError('Missing native I/O-register pin')
            node=ptr(pin+0x18);actual[label]=list(struct.unpack('<3h',C.string_at(node,6))) if node else None
        if actual!=record['pins']:raise ValueError('Native output register endpoints differ from own placement')
        C.memmove(io+0x840,struct.pack('<171h',*record['attributes']),342)
        rows.append(dict(instance=record['instance'],native_name=mapping['instances'][record['instance']],kind='IOFF',site=[r,c,1],
            io_site=record['site'],iobuf=record['iobuf'],pins=actual,attributes=record['attributes']))
    return rows,check
