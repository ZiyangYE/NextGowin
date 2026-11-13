"""Bind a frequency-qualified physical-cone result to raw regression receipts."""
import argparse,copy,json,re,time
from pathlib import Path
from ownpr_device import ROOT
from ownpr_image_storage import load_json,sha
from workspace_budget import reserve
from run_ownpr_pin_closure import metric


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for key in ('attempt','candidate','guards'):p.add_argument('--'+key,type=Path,required=True)
    a=p.parse_args();a.attempt=a.attempt.resolve();a.candidate=a.candidate.resolve();a.guards=a.guards.resolve();reserve(ROOT,4_000_000)
    budget=load_json(a.attempt/'budget.json');summary=load_json(a.attempt/'engineering_summary.json');guard=load_json(a.guards/'native_guard_audit_002.json');raw=load_json(a.guards/'manifest.json')
    if summary['status']!='qualified_joint_lut_candidate' or not summary['within_original_deadline']:raise ValueError('Primary frequency not qualified within original deadline')
    if guard['status']!='three_raw_guards_passed_actual_sta' or not all(r['qualified'] and not r['fallback_used'] for r in guard['cases']):raise ValueError('Raw guard safety not qualified')
    selected=load_json(a.attempt/'qualified_replay_checkpoint.json')['designs'][0]
    for file,digest in selected['selected_source_sha256'].items():
        if sha(ROOT/file)!=digest:raise ValueError('Qualified primary artifact changed')
    search=load_json(a.candidate/'manifest.json');model=load_json(a.attempt/'native_model_validation.json');state={k:ROOT/v for k,v in selected['selected'].items()};native=state['sta'];nm=load_json(native/'manifest.json');na=load_json(native/'handoff_audit.json');old=load_json(a.attempt/'export/native/manifest.json')
    if not search['proof']['checks'] or not all(r['cec']['equivalent'] for r in search['proof']['checks']):raise ValueError('Missing logic equivalence')
    for path,digest in model['sources'].items():
        if sha(Path(path))!=digest:raise ValueError('Native/model comparison input changed')
    for case in raw['cases']:
        for name in ('ownpr_joint_lut_search.py','ownpr_path_cone.py','ownpr_carry_feedback.py','ownpr_router.py','ownpr_lut_graph_feedback.py','ownpr_alias_feedback.py'):
            if case['operator_sources'][name]!=search['source_sha256'][name]:raise ValueError('Guard and primary algorithms differ')
    for field in ('packed_sha256','placement_sha256','routes_sha256','clock_tree_sha256','partial_configuration_sha256','cst_sha256','io_reference_sha256'):
        if nm[field]!=old[field]:raise ValueError('Qualification changed physical implementation: '+field)
    if any(nm[field] for field in ('vendor_placement_executed','vendor_routing_executed')) or not nm['sta_completed'] or not nm['bitstream_generated']:raise ValueError('Invalid native handoff')
    original_sdc=ROOT/'results/vexriscv_110_reference_001/clock.sdc';clock=ROOT/selected['constraints']['sdc']
    normalize=lambda f:re.sub(r'(create_clock\s+-name\s+clk\s+-period\s+)[\d.]+',lambda m:m[1]+'<period>',f.read_text())
    if normalize(original_sdc)!=normalize(clock):raise ValueError('Qualification changed nonperiod timing constraints')
    qm=metric(native)
    if not qm['native_constraints_satisfied'] or not qm['other_checks_passed'] or qm['setup_violations'] or qm['hold_violations']:raise ValueError('Qualified actual STA no longer passes')
    if not na['io']['pins_preserved'] or not na['io']['electrical_report_matched']:raise ValueError('I/O contract differs')
    if model['status']!='changed_netlist_endpoint_envelopes_validated' or any(max(r['maximum_errors_ns'].values())>=.002 for r in model['checks'].values()):raise ValueError('Feedback not independently validated')
    for name,r in model['checks'].items():
        if r['endpoints']!=na['timing']['path_coverage'][name]['paths']:raise ValueError('Qualification endpoint coverage differs')
    joint=load_json(a.attempt/'export/joint_route_audit.json')
    if not joint['no_data_clock_node_conflicts'] or not joint['carry_capacity_verified']:raise ValueError('Joint occupancy audit failed')
    rows=[copy.deepcopy(selected)]
    rows[0].update(three_design_raw_guard_safety_proven=True,new_algorithm_performance_generalization_proven=False,guard_audit=str((a.guards/'native_guard_audit_002.json').relative_to(ROOT)),goal_complete=False,target_achieved=False)
    for case in raw['cases']:
        if case['status']!='unchanged_raw_decision':raise ValueError('This composite writer requires unchanged raw guard outputs')
        r=copy.deepcopy(case['source_checkpoint']);check=next(x for x in guard['cases'] if x['design']==case['design'])
        for file,digest in r['selected_source_sha256'].items():
            if sha(ROOT/file)!=digest:raise ValueError('Frozen guard artifact changed')
        r.update(design=case['design'],qualified=True,qualified_mhz=check['qualification_mhz'],raw_algorithm_decision='unchanged after measured search',fallback_used=False,new_algorithm_performance_generalization_proven=False)
        rows.append(r)
    checkpoint=a.attempt/'all_designs_qualified_checkpoint.json'
    with checkpoint.open('x') as stream:json.dump(dict(designs=rows,goal_complete=False,primary_target_mhz=110),stream,indent=2)
    t=time.time();record=dict(status='qualified_primary_with_three_raw_guards',goal_complete=False,target_mhz=110,qualified_mhz=selected['qualified_mhz'],
        metrics_110mhz=summary['metrics_110mhz'],qualification_metrics=qm,model_validation=model['checks'],lut_delta=search['lut_delta'],register_delta=search['register_delta'],
        ordinary_nets=joint['ordinary_nets'],clock_sinks=joint['clock_sinks'],carry_links=joint['carry_links'],physical_pips_checked=search['connectivity_audit']['pips_checked'],
        io_ports=na['io']['ports'],hardware_verified=False,bitstream_generated=True,vendor_pnr_executed=False,open_chip_database_used=False,three_design_raw_guard_safety_proven=True,
        new_algorithm_performance_generalization_proven=False,complete_attempt_elapsed_seconds=t-budget['start_epoch'],deadline_epoch=budget['deadline_epoch'],within_original_deadline=t<=budget['deadline_epoch'],
        workspace_bytes=reserve(ROOT,0)['current_bytes'],source_sha256={str(f.relative_to(ROOT)):sha(f) for f in [a.attempt/'engineering_summary.json',a.attempt/'native_model_validation.json',a.guards/'native_guard_audit_002.json',checkpoint,Path(__file__).resolve()]},
        remaining_requirements=['VexRiscv 110 MHz actual complete I/O STA','Broad clock/state/macro movement feedback and uncalibrated resource modes remain outside this fixed-boundary pass','No general performance superiority established'])
    if not record['within_original_deadline']:raise TimeoutError('Original complete attempt deadline')
    with (a.attempt/'completion_audit.json').open('x') as stream:json.dump(record,stream,indent=2)
    print(json.dumps({k:record[k] for k in ('status','qualified_mhz','qualification_metrics','complete_attempt_elapsed_seconds','workspace_bytes','goal_complete')},indent=2))


if __name__=='__main__':main()
