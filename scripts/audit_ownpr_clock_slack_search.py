"""Audit an unchanged slack-aware clock search and its bounded neighborhood.

Rebuild the timing model and proposal sequence. Intermediate native queries are
not re-executed; unchanged final implementation retains its full native STA.
"""
import argparse,json,shutil
from pathlib import Path
from ownpr_device import ROOT
from ownpr_place import sha
from workspace_budget import reserve
from ownpr_clock_slack import compile_model,ClockSlackModel,score
from ownpr_clock_slack_search import reconnect_group,identity,ordered_proposals
from ownpr_clock_graph import ClockGraph
from run_ownpr_clock_closure import clock_identity
from run_ownpr_pin_closure import metric
from audit_ownpr_native_export import audit as export_audit
from audit_ownpr_joint_routes import audit as joint_audit


def read(p):return json.loads(Path(p).read_text())


def audit(attempt):
    attempt=Path(attempt);meta=read(attempt/'manifest.json');folder=attempt/'search';search=read(folder/'manifest.json');report=read(folder/'search_report.json');s=report['slack_search']
    if meta['status']!='bounded_clock_attempt_complete' or not meta['clock_paths_unchanged'] or not meta['slack_driven']:raise ValueError('Audit requires a completed unchanged slack-aware attempt')
    if meta['selected']!=meta['initial'] or meta['metrics']!=meta['initial_metrics']:raise ValueError('Unchanged search changed its selected state')
    if meta['search_report_sha256']!=sha(folder/'search_report.json') or search['search_report.json_sha256']!=sha(folder/'search_report.json'):raise ValueError('Clock search report changed')
    for name,digest in search['source_sha256'].items():
        if sha(folder/name)!=digest:raise ValueError('Clock search input or snapshot changed')
    for name,digest in meta['source_sha256'].items():
        if sha(Path(name))!=digest:raise ValueError('Baseline source changed')
    if sha(attempt/'clock_feedback.json')!=sha(folder/'slack_feedback.json'):raise ValueError('Clock feedback differs from worker input')
    state=meta['selected'];placement=Path(state['placement']);routes=Path(state['routes']);sta=Path(state['sta'])
    native=export_audit(sta,placement);joint=joint_audit(placement,routes,state['wire'])
    if metric(sta)!=meta['metrics']:raise ValueError('Selected native timing changed')
    model_data=read(folder/'slack_feedback.json');fresh=json.loads(json.dumps(compile_model(placement,sta)))
    if fresh!=model_data:raise ValueError('Clock feedback does not reproduce from audited evidence')
    model=ClockSlackModel(fresh);tree=read(folder/'clock_tree.json');base=read(routes/'clock_tree.json')
    if clock_identity(tree)!=clock_identity(base):raise ValueError('Clock search actually changed paths')
    measured=read(folder/'clock_wire_delays.json')
    if sha(folder/'clock_tree.json')!=search['clock_tree.json_sha256'] or sha(folder/'clock_wire_delays.json')!=search['clock_wire_delays.json_sha256']:raise ValueError('Clock output changed')
    if model.evaluate(measured['sinks'])!=s['selected_metrics'] or s['accepted']:raise ValueError('Unchanged search has inconsistent predicted metrics')
    graph=ClockGraph(ROOT/'results/native_clock_inventory_005')
    paths={graph.by_coordinate[tuple(p[0])]:tuple(graph.by_coordinate[tuple(n)] for n in reversed(p)) for p in base['paths_sink_to_source']}
    costs={graph.by_coordinate[tuple(r['node'])]:[(None,None,None,tuple(a['path_ids'])) for a in r['alternatives']] for r in report['candidate_costs']}
    if set(costs)!=set(paths):raise ValueError('Search candidate endpoint coverage changed')
    expected=[];seen={identity(paths)}
    for target,option in ordered_proposals(costs,graph,model.priorities()):
        try:changed=reconnect_group(paths,option[3]);key=identity(changed)
        except ValueError:continue
        if key in seen:continue
        seen.add(key);expected.append(dict(proposal_sink=graph.nodes[target]['coordinate'],proposal_path_ids=list(option[3]),changed_sinks=sum(paths[n]!=p for n,p in changed.items())))
    if len(s['trials'])>len(expected):raise ValueError('Duplicate or extra search trials')
    for trial,proposal in zip(s['trials'],expected):
        if trial['pass_number']!=1 or any(trial[k]!=v for k,v in proposal.items()):raise ValueError('Search proposal order/identity changed')
        if 'metrics' in trial:
            ranked=score(trial['metrics'])
            if (ranked is None)!=(trial['status']=='conservative_guard_rejected'):raise ValueError('Clock guard decision changed')
            if ranked is not None and ranked>score(s['initial_metrics']):raise ValueError('Unchanged search ignored an improving measured proposal')
    complete=s['status']=='local_candidates_exhausted'
    if complete and len(s['trials'])!=len(expected):raise ValueError('Claimed complete neighborhood is truncated')
    return dict(status='unchanged_clock_feedback_audited',selected=state,metrics=meta['metrics'],
        evaluated_candidates=len(s['trials']),distinct_single_prefix_candidates=len(expected),single_prefix_neighborhood_complete=complete,
        conservative_guard_rejections=sum(t['status']=='conservative_guard_rejected' for t in s['trials']),
        model_rebuilt_exactly=True,baseline_arrival_error_ns=fresh['baseline_arrival_error_ns'],
        configuration_bits_verified=native['configuration_bits_verified'],joint_route_audit=joint,
        source_sha256={str(p.resolve()):sha(p) for p in (attempt/'manifest.json',folder/'manifest.json',folder/'search_report.json',folder/'slack_feedback.json',folder/'clock_tree.json',folder/'clock_wire_delays.json')},
        intermediate_native_queries_reexecuted=False,global_clock_optimality_proved=False,
        vendor_placement_executed=False,vendor_routing_executed=False,open_chip_database_used=False,timing_signoff=False,full_bitstream_verified=False)


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--attempt',type=Path,required=True);p.add_argument('--out',type=Path,required=True);a=p.parse_args()
    reserve(ROOT,5_000_000);a.out.mkdir(exist_ok=False);shutil.copyfile(__file__,a.out/'auditor_source.py.txt')
    result=audit(a.attempt);result['auditor_sha256']=sha(a.out/'auditor_source.py.txt');(a.out/'summary.json').write_text(json.dumps(result,indent=2))
    print(json.dumps({k:result[k] for k in ('status','evaluated_candidates','single_prefix_neighborhood_complete','metrics')},indent=2))
