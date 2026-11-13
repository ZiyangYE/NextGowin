"""Read actual native clock parent chains after a labelled reference P&R."""
import ctypes as C,json,struct


def capture(out,fn,mem,vector,getname,classname,string,pinnodes):
    vp=C.c_void_p;ptr=lambda p:C.c_uint64.from_address(p).value
    label_fn=fn('?getPinNameForRpt@Pin@NETLIST@@QEAAXAEAV?$basic_string@DU?$char_traits@D@std@@V?$allocator@D@2@@std@@@Z',None,[vp,vp])
    parents={};sinks=[];roots=set();ambiguities=[]
    for address in range(vector[0],vector[1],8):
        obj=ptr(address);cls=classname(obj)
        family=next((x for x in ('Reg','Bsram','NewDsp') if cls=='.?AV'+x+'@NETLIST@@'),None)
        if family is None:continue
        buffer=C.create_string_buffer(32);getname(obj,C.addressof(buffer));name=string(C.addressof(buffer))
        pins=(C.c_uint64*3)();fn(f'?getAllInPins@{family}@NETLIST@@UEAA?AV?$vector@PEAVPin@NETLIST@@V?$allocator@PEAVPin@NETLIST@@@std@@@std@@XZ',vp,[vp,vp])(obj,C.addressof(pins))
        for offset in range(pins[0],pins[1],8):
            pin=ptr(offset);buf=C.create_string_buffer(32);C.c_uint64.from_address(C.addressof(buf)+24).value=15;label_fn(pin,C.addressof(buf));label=string(C.addressof(buf)).rsplit('.',1)[-1]
            if label not in ('CLK','CLKA','CLKB','CLK[0]','CLK[1]'):continue
            nodes=(C.c_uint64*3)();pinnodes(pin,C.addressof(nodes),mem)
            for at in range(nodes[0],nodes[1],8):
                node=ptr(at);chain=[];seen=set()
                while node:
                    if node in seen or len(seen)>1024:raise ValueError('Native clock parent cycle/unbounded chain')
                    seen.add(node);point=tuple(struct.unpack('<3h',C.string_at(node,6)));parent=ptr(node+0x18)
                    before=tuple(struct.unpack('<3h',C.string_at(parent,6))) if parent else None
                    if point in parents and parents[point]!=before:ambiguities.append(dict(node=point,parents=[parents[point],before]))
                    parents[point]=before;chain.append(point);node=parent
                if chain:roots.add(chain[-1]);sinks.append(dict(instance=name,pin=label,path=chain))
    result=dict(status='native_reference_clock_chains_observed',roots=sorted(roots),sinks=sinks,parents=[dict(node=k,parent=v) for k,v in sorted(parents.items())],ambiguities=ambiguities,
        vendor_pnr_executed=True,own_clock_tree=False,scope='Read-only native reference clock routes; not an own-P&R implementation.')
    (out/'native_clock_chains.json').write_text(json.dumps(result,indent=2));return result
