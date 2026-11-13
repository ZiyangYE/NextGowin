"""Bind independently placed, isolated arithmetic CFUs in the native adapter.

No native placement/routing is invoked. Binding and configuration fragments
do not establish dedicated carry timing or complete device legality.
"""
import ctypes as C
import struct
from collections import defaultdict
from ownpr_device import Device
from ownpr_carry_macros import validate
from ownpr_alu_configuration import Mode2Configuration


def bind_arithmetic(native,design,placement,mapping,bind):
    device=Device();audit=validate(design,placement,device);model=Mode2Configuration(device)
    ptr=native.ptr;vp=C.c_void_p
    setpins=native.fn('?setPin2Node@Alu@NETLIST@@UEAAXXZ',None,[vp])
    getinit=native.fn('?getInitValue@Alu@NETLIST@@QEAA?AV?$basic_string@DU?$char_traits@D@std@@V?$allocator@D@2@@std@@XZ',vp,[vp,vp])
    tiles=defaultdict(list);records=[]
    for name,row in placement.get('arithmetic_clusters',{}).items():tiles[row['row'],row['column']].append((name,row))
    for (r,c),occupants in tiles.items():
        aggregate=ptr(native.getgrid(native.chip,r+1,c+1)+0x70)
        from ownpr_carry_lut_sharing import attributes
        attrs,_=attributes(placement,r,c,[row['slot'] for _,row in occupants])
        for i,values in enumerate(attrs):
            address=ptr(aggregate+0x360+8*i);raw=struct.pack('<19h',*values);C.memmove(address+6,raw,len(raw))
        for name,row in occupants:
            slot=row['slot'];physical=ptr(aggregate+0x40+8*slot);obj=bind(name,physical)
            C.c_uint64.from_address(physical+0x20).value=obj;setpins(obj)
            buf=C.create_string_buffer(32);getinit(obj,C.addressof(buf));p=C.addressof(buf);length=ptr(p+16)
            if length>32:raise ValueError('Invalid native arithmetic parameter')
            mode=int(C.string_at(ptr(p) if ptr(p+24)>=16 else p,length).decode())
            if mode!=row['mode']:raise ValueError('Native reader changed arithmetic mode')
            header=list(struct.unpack('<4h',C.string_at(physical+0x10,8)))
            if header[1:]!=[r+1,c+1,slot]:raise ValueError('Arithmetic physical header differs from own placement')
            records.append(dict(instance=name,native_name=mapping['instances'][name],kind='ALU',site=[r+1,c+1,slot],
                mode=mode,input_profile=row['input_profile'],expected_truth_word=model.word(row['mode'],row['input_profile']),
                cached_logic_matches=ptr(physical+0x20)==obj,carry_timing_verified=False))
    return records,audit
