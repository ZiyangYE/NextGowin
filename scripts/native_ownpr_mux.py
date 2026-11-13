"""Install own dedicated mux placement in the native STA/encoder objects."""
import ctypes as C
import struct
from ownpr_device import Device
from ownpr_mux_architecture import MuxArchitecture
from ownpr_mux_configuration import compile_wide


def bind_muxes(native,design,placement,mapping,bind):
    device=Device();model=MuxArchitecture();configuration=compile_wide(design,placement,device);ptr=native.ptr;vp=C.c_void_p
    setpins=native.fn('?setPin2Node@Mux2@NETLIST@@UEAAXXZ',None,[vp]);getsub=native.fn('?getSubType@Mux2@NETLIST@@UEAA?AW4INS_TYPE@2@XZ',C.c_int,[vp])
    getphy=native.fn('?getPhyMux2@Mux2@NETLIST@@QEAAPEAVmux2@2@XZ',vp,[vp]);records=[]
    for row in configuration['sites']:
        aggregate=ptr(native.getgrid(native.chip,row['row']+1,row['column']+1)+0x70)
        for i,attrs in enumerate(row['groups']):
            raw=struct.pack('<19h',*attrs);C.memmove(ptr(aggregate+0x360+8*i)+6,raw,len(raw))
    for name,row in sorted(placement['mux_clusters'].items(),key=lambda item:(item[1]['level'],item[0])):
        r,c,slot=row['row']+1,row['column']+1,row['slot'];aggregate=ptr(native.getgrid(native.chip,r,c)+0x70);physical=ptr(aggregate+0x150+8*slot)
        if list(struct.unpack('<4h',C.string_at(physical+0x10,8)))!=[row['level']+28,r,c,slot]:raise ValueError('Wrong native dedicated mux site')
        obj=bind(name,physical);C.c_uint64.from_address(physical+0x18).value=obj;setpins(obj)
        if getphy(obj)!=physical or getsub(obj)!=row['level']+28:raise ValueError('Native mux logical/physical binding changed')
        records.append(dict(instance=name,native_name=mapping['instances'][name],kind='MUX',level=row['level'],site=[r,c,slot],cached_logic_matches=ptr(physical+0x18)==obj))
    return records,configuration
