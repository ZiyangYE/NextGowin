"""Audit physical shared-register attempts without treating capacity as speed."""
import argparse,json
from pathlib import Path
from ownpr_device import ROOT
from ownpr_image_storage import load_json,sha
from ownpr_pin_cut_selection import measured_inputs
from summarize_pin_frontier import paths
from summarize_path_regions import endpoint_table
from audit_packing_gap import own
from audit_shared_ff_route import audit as shared_audit
from workspace_budget import reserve


def summarize(baseline,run):
    old=load_json(baseline)['designs'][0];m=load_json(run/'manifest.json')
    if m['status'] not in ('accepted','rejected') or not 0<m['wall_seconds']<=m['budget_seconds']<=3600:raise ValueError('Incomplete or over-budget shared attempt')
    for name,h in old['selected_source_sha256'].items():
        if sha(ROOT/name)!=h:raise ValueError('Baseline changed')
    oldp=ROOT/old['selected']['placement'];oldsta=ROOT/old['selected']['sta'];newp=run/'candidate';native=run/'implementation/export/native'
    if sha(oldp/'packed.json')!=sha(newp/'packed.json'):raise ValueError('Shared packing changed logical bytes')
    op=load_json(oldp/'placement.json');np=load_json(newp/'placement.json');em=load_json(run/'implementation/export/manifest.json');nm=load_json(native/'manifest.json')
    if op['fixed_io']!=np['fixed_io'] or op['hard_clusters']!=np['hard_clusters']:raise ValueError('Fixed I/O or hard sites changed')
    if em['status']!='verified_handoff_complete' or nm['vendor_placement_executed'] or nm['vendor_routing_executed'] or nm['cst_sha256']!=sha(ROOT/old['constraints']['cst']):raise ValueError('Own implementation contract failed')
    shared=shared_audit(native,newp)
    ow,owm=measured_inputs(ROOT/old['selected']['routes'],ROOT/old['selected']['wire'],oldp);nw,nwm=measured_inputs(run/'implementation/route',run/'implementation/export/wire',newp)
    oldends,oprd=endpoint_table(oldsta);newends,nprd=endpoint_table(native)
    if set(oldends)!=set(newends):raise ValueError('State-preserving packing changed endpoint coverage')
    endpoint_changes=[dict(endpoint=n,delta_ns=newends[n]-(oldends[n]-(oprd-nprd)),new_slack_ns=newends[n]) for n in newends]
    traces=paths(native)
    for trace in traces:
        for arc in trace['arcs']:
            key=tuple(arc['endpoint'].rsplit('/',1))
            if arc['kind']=='tNET' and key in ow and key in nw:
                arc.update(old_max_wire_ns=ow[key],new_max_wire_ns=nw[key],wire_delta_ns=nw[key]-ow[key])
    stats=[]
    for p in (oldp,newp):
        row=own(p);stats.append({k:v for k,v in row.items() if k not in ('control_groups','pair_bound_scope')})
    oldff={r['ff']:tuple(r[k] for k in ('row','column','slot')) for r in op['clusters'].values() if r.get('ff')};newff={r['ff']:tuple(r[k] for k in ('row','column','slot')) for r in np['clusters'].values() if r.get('ff')}
    if set(oldff)!=set(newff):raise ValueError('State binding inventory changed')
    result=dict(status='shared_packing_attempt_audited',run=run.as_posix(),attempt_status=m['status'],wall_seconds=m['wall_seconds'],budget_seconds=m['budget_seconds'],
        metrics=m['metrics'],qualified_mhz=m.get('qualified_mhz'),qualified_metrics=m.get('qualified_metrics'),target_achieved=m['target_achieved'],logical_netlist_byte_identical=True,fixed_io_hard_sites_unchanged=True,
        moved_registers=sum(oldff[n]!=newff[n] for n in oldff),moved_lut_sites=sum(tuple(r[k] for k in ('row','column','slot'))!=tuple(np['clusters'][n][k] for k in ('row','column','slot')) for n,r in op['clusters'].items()),
        packing_before_after=stats,actual_shared_bindings=shared,plan=load_json(newp/'plan.json'),worst_paths=traces,
        endpoint_comparison=dict(count=len(endpoint_changes),old_period_ns=oprd,target_period_ns=nprd,improved_over_10ps=sum(r['delta_ns']>.01 for r in endpoint_changes),regressed_over_10ps=sum(r['delta_ns']<-.01 for r in endpoint_changes),largest_regressions=sorted(endpoint_changes,key=lambda r:r['delta_ns'])[:20]),
        source_sha256=dict(baseline=sha(baseline),run_manifest=sha(run/'manifest.json'),handoff_audit=sha(native/'handoff_audit.json')),wire_sources=dict(before=owm,after=nwm),
        vendor_pnr_executed=False,goal_complete=False,scope='Same logical CPU; actual implementation and full IO STA. Different paths may become critical; density and span are not frequency gains.')
    return result


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--baseline',type=Path,required=True);p.add_argument('--run',type=Path,required=True);p.add_argument('--out',type=Path,required=True);a=p.parse_args()
    reserve(ROOT,2_000_000);r=summarize(a.baseline,a.run)
    if a.out.exists():raise FileExistsError(a.out)
    a.out.parent.mkdir(parents=True,exist_ok=True);a.out.write_text(json.dumps(r,indent=2))
    print(json.dumps({k:r[k] for k in ('status','attempt_status','wall_seconds','metrics','qualified_mhz','moved_registers','moved_lut_sites')},indent=2))
