"""Verify routed SEL data entries, native ownership and independent FF modes."""
from pathlib import Path
from ownpr_device import Device
from ownpr_image_storage import load_json,sha
from ownpr_independent_ff_inputs import validate


def audit(folder,placement):
    folder=Path(folder);placement=Path(placement);design=load_json(placement/'packed.json');placed=load_json(placement/'placement.json');device=Device()
    records=validate(design,placed,device);captures=load_json(folder/'sta_endpoint_audit.json');expanded=load_json(folder/'expanded_routes.json');binding=load_json(folder/'binding_report.json');mapping=load_json(folder/'name_map.json')
    if not records or not binding['encoding_preserved_parents'] or not binding['encoding_preserved_geometry'] or binding.get('sta_report_returncode')!=0:raise ValueError('Independent FF lacks complete stable native handoff')
    native={r['ff']:r for r in binding.get('independent_ff_input_bindings',[])};checked=[];cells=design['modules']['top']['cells']
    for r in records:
        ff=r['ff'];point=[r['row']+1,r['column']+1,device.ids[r['selector']]];items=[c for c in captures if c['instance']==ff and c['pin']=='D' and c['direction']=='In']
        if len(items)!=1 or items[0]['direct_connection'] or items[0]['native_name']!=mapping['instances'][ff] or len(items[0]['nodes'])!=1:raise ValueError('Independent FF input was omitted or treated as a direct LUT link')
        captured=items[0]['nodes'][0];net='net_'+str(cells[ff]['connections']['D'][0]);tree=expanded['nets'][net];parents={tuple(v['node']):v['parent'] for v in tree['parents']};chain=[];node=point;seen=set()
        while node is not None:
            key=tuple(node)
            if key in seen or key not in parents:raise ValueError('Independent FF input tree is incomplete/cyclic')
            seen.add(key);chain.append(node);node=parents[key]
        if tree['clock'] or chain[-1] not in tree['sources'] or captured['node']!=point or captured['chain']!=chain:raise ValueError('Independent FF input differs from own routed source/path')
        if ff not in native or native[ff]['selector_node']!=point or native[ff]['mode']!=1 or not native[ff]['routed_flag_set']:raise ValueError('Independent FF native mode/binding was not applied')
        checked.append(dict(**r,node=point,chain=chain,wire_delays=captured['wire_delays']))
    return dict(status='independent_ff_native_modes_endpoints_and_parents_matched',records=checked,
        source_sha256={str(p):sha(p) for p in (folder/'sta_endpoint_audit.json',folder/'expanded_routes.json',placement/'packed.json',placement/'placement.json')},
        scope='Additional independent input gate; complete configuration readback, full I/O timing and setup/hold acceptance are still required.')
