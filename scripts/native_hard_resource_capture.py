"""Observe native BRAM/DSP physical pin dispatch; calibration, not own P&R."""
import ctypes as C,json,struct
from collections import Counter


def capture(out,fn,mem,vector,getname,getphy,classname,string,pinnodes,vendor_pnr_executed=True):
    vp=C.c_void_p;rows=[];classes=Counter()
    gettype=fn('?getType@Instance@NETLIST@@QEAA?AW4INS_TYPE@2@XZ',C.c_int,[vp])
    pinlabel=fn('?getPinNameForRpt@Pin@NETLIST@@QEAAXAEAV?$basic_string@DU?$char_traits@D@std@@V?$allocator@D@2@@std@@@Z',None,[vp,vp])
    for address in range(vector[0],vector[1],8):
        obj=C.c_uint64.from_address(address).value;typ=classname(obj);classes[typ]+=1
        cls=next((s for s in ('Bsram','BsramEcc','Dsp','NewDsp') if typ=='.?AV'+s+'@NETLIST@@'),None)
        if cls is None:continue
        name=C.create_string_buffer(32);getname(obj,C.addressof(name));phy=getphy(obj)
        row=dict(instance=string(C.addressof(name)),logical_class=cls,physical_class=classname(phy),type_enum=gettype(obj),
            subtype_enum=fn(f'?getSubType@{cls}@NETLIST@@UEAA?AW4INS_TYPE@2@XZ',C.c_int,[vp])(obj),
            site=list(struct.unpack('<4h',C.string_at(phy+0x10,8))) if phy else None,pins=[])
        if phy:
            row['observed_logical_reference_offsets']=[offset for offset in range(8,0x48,8) if C.c_uint64.from_address(phy+offset).value==obj]
            if cls=='Bsram':
                row['physical_attributes']=list(struct.unpack('<93H',C.string_at(phy+0x8c0,186)))
                row['encoder_typed_cache_matches']=C.c_uint64.from_address(phy+0x678).value==obj
                row['encoder_typed_cache_class']=classname(C.c_uint64.from_address(phy+0x678).value)
            elif cls=='NewDsp' and row['physical_class']=='.?AVmultalu27x18@NETLIST@@':
                from native_hard_binding import DSP_ATTRIBUTE_COUNT
                row['physical_attributes']=list(struct.unpack('<'+str(DSP_ATTRIBUTE_COUNT)+'H',C.string_at(phy+0x22,DSP_ATTRIBUTE_COUNT*2)))
            if cls=='NewDsp':
                parent=fn('?getNewPhyDsp@NewDsp@NETLIST@@QEAAPEAVnewDsp@2@XZ',vp,[vp])(obj)
                row['parent_class']=classname(parent)
                row['parent_logical_reference_offsets']=[offset for offset in range(8,0x48,8) if C.c_uint64.from_address(parent+offset).value==obj] if parent else []
        for direction in ('In','Out'):
            pins=(C.c_uint64*3)();fn(f'?getAll{direction}Pins@{cls}@NETLIST@@UEAA?AV?$vector@PEAVPin@NETLIST@@V?$allocator@PEAVPin@NETLIST@@@std@@@std@@XZ',vp,[vp,vp])(obj,C.addressof(pins))
            if not pins[0]<=pins[1]<=pins[2] or (pins[1]-pins[0])%8 or pins[1]-pins[0]>8*4096:raise ValueError('Invalid hard-resource pin vector')
            for pos in range(pins[0],pins[1],8):
                pin=C.c_uint64.from_address(pos).value;label=C.create_string_buffer(32);C.c_uint64.from_address(C.addressof(label)+24).value=15;pinlabel(pin,C.addressof(label));label=string(C.addressof(label))
                nodes=(C.c_uint64*3)();ok=bool(pinnodes(pin,C.addressof(nodes),mem))
                if not nodes[0]<=nodes[1]<=nodes[2] or (nodes[1]-nodes[0])%8 or nodes[1]-nodes[0]>8*4096:raise ValueError('Invalid hard-resource node vector')
                points=[list(struct.unpack('<3h',C.string_at(C.c_uint64.from_address(q).value,6))) for q in range(nodes[0],nodes[1],8)]
                row['pins'].append(dict(label=label,pin=label.rsplit('.',1)[-1],direction=direction,native_pin_type=C.c_int.from_address(pin+0x48).value,nodes=points,query_return=ok))
        rows.append(row)
    result=dict(status='observed_native_hard_resource_bindings',instances=rows,logical_class_counts=dict(classes),
        vendor_placement_executed=vendor_pnr_executed,vendor_routing_executed=vendor_pnr_executed,own_pnr_result=False,independent_placement_verified=False,configuration_encoding_verified=False,timing_dispatch_verified=False)
    (out/'hard_resource_bindings.json').write_text(json.dumps(result,indent=2));return result
