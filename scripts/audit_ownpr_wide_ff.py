"""Require native FF DI endpoints and selected parents in a complete handoff."""
import argparse,hashlib,json
from pathlib import Path
from ownpr_device import Device
from ownpr_wide_ff_packing import pairs
from ownpr_image_storage import load_json,sha


def audit(folder,placement):
    folder=Path(folder);placement=Path(placement);meta=load_json(folder/'manifest.json')
    design=load_json(placement/'packed.json');placed=load_json(placement/'placement.json');mapping=load_json(folder/'name_map.json')
    binding=load_json(folder/'binding_report.json')
    # sta_completed is set only after this very audit succeeds. Require the
    # generated report and worker return code, not the later acceptance flag.
    if meta['status']!='imported' or not meta['sta_report_generated'] or binding.get('sta_report_returncode')!=0 or meta['packed_sha256']!=sha(placement/'packed.json') or meta['placement_sha256']!=sha(placement/'placement.json'):
        raise ValueError('Wide FF handoff does not match completed native STA')
    records=pairs(design,placed,Device());captures=load_json(folder/'sta_endpoint_audit.json');expanded=load_json(folder/'expanded_routes.json')
    bindings={r['instance']:r for r in binding['bindings']};checked=[]
    if not binding['encoding_preserved_parents'] or not binding['encoding_preserved_geometry']:raise ValueError('Encoding changed selected wide FF implementation')
    cells=design['modules']['top']['cells']
    for p in records:
        r,c,s=p['row']+1,p['column']+1,p['slot'];src=[r,c,48+s];dst=[r,c,295+s];ff=p['ff']
        selected=[q for q in captures if q['instance']==ff and q['pin']=='D' and q['direction']=='In']
        if len(selected)!=1:raise ValueError('Missing or duplicate wide FF D capture')
        q=selected[0]
        if q['native_name']!=mapping['instances'][ff] or q['direct_connection'] or len(q['nodes'])!=1:raise ValueError('Wide FF data was substituted with a LUT-paired bypass')
        node=q['nodes'][0]
        if node['node']!=dst or node['chain']!=[dst,src]:raise ValueError('Wide FF selected parent differs from root/FF relation')
        net='net_'+str(cells[ff]['connections']['D'][0]);data=expanded['nets'][net]
        parents={tuple(v['node']):v['parent'] for v in data['parents']}
        if data['clock'] or parents.get(tuple(dst))!=src or parents.get(tuple(src),'absent') is not None:raise ValueError('Wide FF input is not rooted at the actual wide output')
        if bindings[ff]['kind']!='FF' or bindings[ff]['site']!=[r,c,s] or bindings[p['root']]['site']!=[r,c,s]:raise ValueError('Wide FF physical site changed')
        checked.append(dict(ff=ff,root=p['root'],level=p['level'],source=src,destination=dst,chain=node['chain'],wire_delays=node['wire_delays'],configuration_calibration_sha256=p['calibration_sha256']))
    if not records:raise ValueError('No wide FF pairs to audit')
    stable_binding={k:binding[k] for k in ('bindings','encoding_preserved_parents','encoding_preserved_geometry')}
    return dict(status='wide_ff_native_endpoints_and_parents_matched',records=checked,
        binding_geometry_sha256=hashlib.sha256(json.dumps(stable_binding,sort_keys=True,separators=(',',':')).encode()).hexdigest(),
        source_sha256={str(p):sha(p) for p in (folder/'sta_endpoint_audit.json',folder/'expanded_routes.json',placement/'packed.json',placement/'placement.json')},
        hardware_verified=False,scope='Additional mode/endpoint/selected-parent gate. Full configuration, report coverage and setup/hold acceptance remain required from the complete handoff audit.')


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--native',type=Path,required=True);p.add_argument('--placement',type=Path,required=True);p.add_argument('--out',type=Path,required=True);a=p.parse_args()
    result=audit(a.native,a.placement);a.out.write_text(json.dumps(result,indent=2));print(json.dumps(result,indent=2))
