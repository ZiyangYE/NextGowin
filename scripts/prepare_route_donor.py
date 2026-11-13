"""Prepare a bounded conflict-closure seed from a matching physical donor tree.

The ordinary router must independently admit every seed and route released
blockers. No candidate is accepted without a new complete native export.
"""
import argparse,copy,json,shutil
from pathlib import Path
from ownpr_image_storage import load_json,sha
from ownpr_incremental_routes import signature
from ownpr_device import ROOT
from workspace_budget import reserve

def prepare(base,donor,net,out,max_blockers=2):
    bm=load_json(base/'manifest.json');dm=load_json(donor/'manifest.json')
    if any(m['status']!='routed' or m['vendor_pnr_executed'] for m in (bm,dm)):raise ValueError('Completed own routes required')
    bn={n['name']:n for n in load_json(base/'nets.json')};dn={n['name']:n for n in load_json(donor/'nets.json')}
    if net not in bn or net not in dn or signature(bn[net])!=signature(dn[net]):raise ValueError('Donor endpoints differ')
    if bn[net].get('clock') or 'constant_value' in bn[net]:raise ValueError('Only ordinary single-source data trees supported')
    br=load_json(base/'routes.json');dr=load_json(donor/'routes.json');tree=dr['nets'][net];nodes=set(tree['nodes'])
    blockers=sorted(n for n,r in br['nets'].items() if n!=net and nodes&set(r['nodes']))
    if len(blockers)>max_blockers:raise ValueError('Conflict closure exceeds bound')
    for n in blockers:
        if bn[n].get('clock') or 'constant_value' in bn[n] or len(bn[n]['sinks'])>64:raise ValueError('Blocker outside bounded ordinary scope')
        if nodes & (set(bn[n]['sinks'])|{bn[n]['source']}):raise ValueError('Donor occupies another net endpoint')
    records={n:dict(copy.deepcopy(r),retained_sinks=bn[n]['sinks']) for n,r in br['nets'].items() if n not in blockers}
    records[net]=dict(copy.deepcopy(tree),retained_sinks=bn[net]['sinks'])
    checkpoint=dict(status='incomplete_route_checkpoint',nets=records,iterations=[],connectivity_verified=False,no_node_conflicts=False,timing_signoff=False)
    reserve(ROOT,60_000_000);out.mkdir(exist_ok=False)
    for name in ('nets.json','all_nets.json','clock_tree.json'):shutil.copyfile(base/name,out/name)
    (out/'partial_routes.json').write_text(json.dumps(checkpoint))
    meta=dict(bm,status='incomplete_route_checkpoint',partial_routes_sha256=sha(out/'partial_routes.json'),donor_net=net,released_blockers=blockers,donor_routes_sha256=sha(donor/'routes.json'),base_routes_sha256=sha(base/'routes.json'),seed_admission_pending=True)
    (out/'manifest.json').write_text(json.dumps(meta,indent=2));return dict(net=net,blockers=blockers,retained_nets=len(records),independent_admission_required=True)

if __name__=='__main__':
    p=argparse.ArgumentParser()
    for n in ('base','donor','out'):p.add_argument('--'+n,type=Path,required=True)
    p.add_argument('--net',required=True);p.add_argument('--max-blockers',type=int,default=2);a=p.parse_args()
    if not 0<=a.max_blockers<=5:p.error('Closure must be bounded to 0..5 blockers')
    print(json.dumps(prepare(a.base,a.donor,a.net,a.out,a.max_blockers)))
