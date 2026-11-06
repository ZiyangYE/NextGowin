"""Read back imported hard pin coordinates and converted physical mode fields."""
import ctypes as C
import json,struct
from ownpr_hard_architecture import HardArchitecture,constants,bits
from ownpr_hard_timing_model import profile


def capture(native,out,design,placed,mapping,bindings,logical_pins,pin_label):
    model=HardArchitecture();contracts=model.contract(design);cells=design['modules']['top']['cells'];ties=constants(design['modules']['top'])
    vp=C.c_void_p;base=native.dll._handle
    storage=C.create_string_buffer(0x188)
    encoder=C.CFUNCTYPE(vp,vp,vp,C.c_bool)(base+0x4f8d40)(C.addressof(storage),native.mem,False)
    if encoder!=C.addressof(storage) or C.c_int.from_address(encoder+0xe0).value!=70:raise ValueError('Hard mode conversion context differs')
    getnodes=native.fn('?getNodebyPin@Pin@NETLIST@@QEAA_NAEAV?$vector@PEAVnode@NETLIST@@V?$allocator@PEAVnode@NETLIST@@@std@@@std@@PEAVglobalMem@@@Z',C.c_bool,[vp,vp,vp])
    records=[]
    by_name={}
    # HardSiteBinding order is canonical name order in the importer.
    for name,binding in zip(sorted(contracts),bindings):
        convert=C.CFUNCTYPE(None,vp,vp)(base+(0x565700 if binding.family=='multalu27x18' else 0x54b6b0))
        convert(encoder,binding.grid)
        size=binding.attribute_size//2;values=list(struct.unpack('<'+str(size)+'H',C.string_at(binding.phy+binding.attribute_offset,binding.attribute_size)))
        wanted=model.mode_attributes[profile(cells[name])]
        if values!=wanted:
            (out/'hard_mode_difference.json').write_text(json.dumps(dict(instance=name,expected=wanted,actual=values,
                differences=[dict(index=i,expected=a,actual=b) for i,(a,b) in enumerate(zip(wanted,values)) if a!=b]),indent=2))
            raise ValueError('Imported hard physical mode differs from independent calibration: '+name)
        by_name[name]=dict(instance=name,primitive=cells[name]['type'],mode_fields=values,pins=[])
    expected={}
    for name,spec in contracts.items():
        loc=placed['hard_clusters'][name]
        expected[name]={label:{(loc['row']+1+r,loc['column']+1+c,i) for r,c,i in e['nodes']} for label,e in spec['endpoints'].items()}
    for name,direction,pin,net in logical_pins:
        if name not in contracts:continue
        label=pin_label(pin);vector=(C.c_uint64*3)();getnodes(pin,C.addressof(vector),native.mem)
        if not vector[0]<=vector[1]<=vector[2] or vector[1]-vector[0]>32768 or (vector[1]-vector[0])%8:raise ValueError('Invalid hard pin node vector')
        observed={tuple(struct.unpack('<3h',C.string_at(native.ptr(a),6))) for a in range(vector[0],vector[1],8)}
        wanted=expected[name].get(label,set());logical={l:(p,i,b) for l,p,i,b in bits(cells[name])};port,index,bit=logical[label]
        inactive={p['pin'] for p in contracts[name]['inactive']};omitted=not observed and (label in inactive or direction=='In' and bit in ties)
        if observed!=wanted and not omitted:raise ValueError('Imported hard endpoint differs from calibrated template: '+name+'/'+label)
        by_name[name]['pins'].append(dict(pin=label,direction=direction,nodes=sorted(observed),calibrated_omission=omitted))
    for name,row in by_name.items():
        labels=[p['pin'] for p in row['pins']]
        if len(labels)!=len(set(labels)) or set(labels)!={label for label,_,_,_ in bits(cells[name])}:raise ValueError('Incomplete hard endpoint readback')
        records.append(row)
    result=dict(status='hard_mode_and_endpoint_readback_matched',hard_cells=len(records),pin_bits=sum(len(r['pins']) for r in records),records=records,
        native_pnr_executed=False,initialization_bits_verified=False,whole_design_routing_verified=False,timing_signoff=False)
    (out/'hard_import_readback.json').write_text(json.dumps(result,indent=2));return result
