"""Compare two full exports with identical logic, placement, clocks and nets."""
import argparse,json
from pathlib import Path
from ownpr_device import ROOT
from ownpr_image_storage import load_json,sha


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--weighted',type=Path,required=True);p.add_argument('--control',type=Path,required=True);p.add_argument('--out',type=Path,required=True);a=p.parse_args()
    from workspace_budget import reserve
    reserve(ROOT,10_000_000)
    wm=load_json(a.weighted/'manifest.json');cm=load_json(a.control/'manifest.json')
    wr=load_json(a.weighted/'route/manifest.json');cr=load_json(a.control/'route/manifest.json')
    wn=load_json(a.weighted/'export/native/manifest.json');cn=load_json(a.control/'export/native/manifest.json')
    we=load_json(a.weighted/'export/manifest.json');ce=load_json(a.control/'export/manifest.json')
    if wm['status']!=cm['status'] or wm['status']!='checkpoint_handoff_complete':raise ValueError('Incomplete physical attempt')
    if we['status']!=ce['status'] or we['status']!='verified_handoff_complete':raise ValueError('Incomplete full export')
    common={}
    for key in ('netlist_sha256','placement_sha256'):
        if wr[key]!=cr[key]:raise ValueError('Different '+key)
        common[key]=wr[key]
    for name in ('nets.json','all_nets.json','clock_tree.json'):
        if sha(a.weighted/'route'/name)!=sha(a.control/'route'/name):raise ValueError('Different physical contract: '+name)
        common[name]=sha(a.weighted/'route'/name)
    for key in ('cst_sha256','source_sdc_sha256'):
        if wn[key]!=cn[key]:raise ValueError('Different constraints')
        common[key]=wn[key]
    for name in ('ownpr_router.py','ownpr_timing_graph.py','ownpr_route_design.py','ownpr_incremental_routes.py'):
        if wr['source_sha256'][name]!=cr['source_sha256'][name]:raise ValueError('Different common routing algorithm')
        common[name]=wr['source_sha256'][name]
    if not wr.get('search_weights_sha256') or cr.get('search_weights_sha256'):raise ValueError('Not a weighted/unweighted control')
    if sha(a.weighted/'route/search_weights.json')!=wr['search_weights_sha256']:raise ValueError('Search weight record changed')
    for m in (wn,cn):
        if m['vendor_placement_executed'] or m['vendor_routing_executed']:raise ValueError('Not an own P&R result')
    result=dict(status='controlled_physical_comparison_verified',common_sha256=common,weighted_metrics=we['metrics'],control_metrics=ce['metrics'],
        report_fmax_gain_percent=(we['metrics']['reported_fmax_mhz']/ce['metrics']['reported_fmax_mhz']-1)*100,
        weighted_route_seconds=wr['total_wall_seconds'],control_route_seconds=cr['total_wall_seconds'],
        weighted_iterations=wr['iterations'],control_iterations=cr['iterations'],historical_priority_policy=wr['historical_priority_policy'],
        weighted_physical_seconds=wm['wall_seconds'],control_physical_seconds=cm['wall_seconds'],
        scope='One controlled same-placement example; reported Fmax is not qualification. Concurrent jobs prevent a general speed-ratio claim. Historical priorities are not current timing budgets.',
        vendor_pnr_executed=False,goal_complete=False)
    a.out.parent.mkdir(parents=True,exist_ok=True);a.out.write_text(json.dumps(result,indent=2))
    print(json.dumps({k:result[k] for k in ('weighted_metrics','control_metrics','report_fmax_gain_percent')},indent=2))


if __name__=='__main__':main()
