"""Audit an own-P&R attempt using the user's direct native-Fmax policy."""
import argparse,copy,json,re,time
from pathlib import Path
from ownpr_device import ROOT
from ownpr_image_storage import load_json,sha
from ownpr_frequency_policy import reported_fmax_acceptance
from workspace_budget import reserve
from run_ownpr_pin_closure import metric


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for key in ('attempt','candidate','guards'):p.add_argument('--'+key,type=Path,required=True)
    a=p.parse_args();a.attempt=a.attempt.resolve();a.candidate=a.candidate.resolve();a.guards=a.guards.resolve();reserve(ROOT,4_000_000)
    summary=load_json(a.attempt/'engineering_summary.json');budget=load_json(a.attempt/'budget.json')
    if summary['status']!='accepted_reported_fmax_candidate' or summary['frequency_replayed'] or (a.attempt/'qualification').exists():raise ValueError('Unexpected frequency replay or unaccepted native Fmax')
    selected=load_json(a.attempt/'reported_fmax_checkpoint.json')['designs'][0]
    for path,h in selected['selected_source_sha256'].items():
        if sha(ROOT/path)!=h:raise ValueError('Selected implementation changed')
    native=ROOT/selected['selected']['sta'];nm=load_json(native/'manifest.json');na=load_json(native/'handoff_audit.json');observed=metric(native)
    period=float(re.search(r'create_clock\s+-name\s+clk\s+-period\s+([\d.]+)',(ROOT/selected['constraints']['sdc']).read_text())[1]);policy=reported_fmax_acceptance(observed,1000/period)
    if not policy['accepted'] or nm['vendor_placement_executed'] or nm['vendor_routing_executed'] or not nm['sta_completed'] or not nm['bitstream_generated']:raise ValueError('Invalid own-P&R native signoff')
    for field,file in [('packed_sha256',a.candidate/'placement/packed.json'),('placement_sha256',a.candidate/'placement/placement.json'),('routes_sha256',a.candidate/'routes.json'),('cst_sha256',ROOT/selected['constraints']['cst']),('source_sdc_sha256',ROOT/selected['constraints']['sdc'])]:
        if nm[field]!=sha(file):raise ValueError('Native implementation/constraint mismatch')
    if not na['io']['pins_preserved'] or not na['io']['electrical_report_matched']:raise ValueError('Changed I/O contract')
    model=load_json(a.attempt/'native_model_validation.json')
    if model['status']!='changed_netlist_endpoint_envelopes_validated':raise ValueError('Model not validated')
    for path,h in model['sources'].items():
        if sha(Path(path))!=h:raise ValueError('Comparison source changed')
    for mode,r in model['checks'].items():
        if r['endpoints']!=na['timing']['path_coverage'][mode]['paths'] or max(r['maximum_errors_ns'].values())>=.002:raise ValueError('Native timing coverage/mismatch')
    search=load_json(a.candidate/'manifest.json');proof=search['proof']['checks'];joint=load_json(a.attempt/'export/joint_route_audit.json')
    if not proof or not all(r['cec']['equivalent'] for r in proof) or not joint['no_data_clock_node_conflicts'] or not joint['carry_capacity_verified']:raise ValueError('CEC/connectivity/capacity gate failed')
    guard=load_json(a.guards/'native_guard_audit_002.json');raw=load_json(a.guards/'manifest.json')
    if guard['status']!='three_raw_guards_passed_actual_sta' or not all(r['qualified'] and not r['fallback_used'] for r in guard['cases']):raise ValueError('Raw guards incomplete')
    rows=[copy.deepcopy(selected)];rows[0].update(three_design_raw_guard_safety_proven=True,new_algorithm_performance_generalization_proven=False,goal_complete=False)
    for case in raw['cases']:
        for name in ('ownpr_joint_lut_search.py','ownpr_shared_dag.py','ownpr_shared_feedback.py','ownpr_lut_graph_feedback.py','ownpr_router.py','ownpr_alias_feedback.py'):
            if case['operator_sources'][name]!=search['source_sha256'][name]:raise ValueError('Primary/guard operation versions differ')
        if case['status']!='unchanged_raw_decision':raise ValueError('Composite requires unchanged raw guard output')
        row=copy.deepcopy(case['source_checkpoint']);check=next(r for r in guard['cases'] if r['design']==case['design'])
        for path,h in row['selected_source_sha256'].items():
            if sha(ROOT/path)!=h:raise ValueError('Frozen guard changed')
        row.update(design=case['design'],qualified=True,qualified_mhz=check['qualification_mhz'],raw_algorithm_decision='unchanged after shared-DAG search',fallback_used=False)
        rows.append(row)
    # Count actually changed macro inputs, not just the enabled capability.
    from ownpr_shared_feedback import normalize_clone_inputs
    from ownpr_mux_macros import tree
    old=ROOT/'results/vexriscv_joint_lut_graph_search_010/placement';reference=load_json(old/'packed.json');layout=load_json(old/'placement.json');private=set()
    for m in layout['wide_macros']:private.update(tree(reference,m['root'],m['root_slot'])['leaves'])
    _,rewires=normalize_clone_inputs(reference,load_json(a.candidate/'placement/packed.json'),layout,load_json(a.candidate/'placement/placement.json'),private)
    checkpoint=a.attempt/'all_designs_reported_fmax_checkpoint.json'
    with checkpoint.open('x') as stream:json.dump(dict(designs=rows,frequency_policy='native_reported_fmax_after_110mhz_sta_with_passing_hold',goal_complete=False),stream,indent=2)
    previous=load_json(ROOT/'results/vexriscv_bram_cone_trial_001/export/manifest.json')['metrics'];elapsed=time.time()-budget['start_epoch']
    if time.time()>budget['deadline_epoch']:raise TimeoutError('Original complete attempt deadline')
    record=dict(status='accepted_native_fmax_with_three_raw_guards',goal_complete=False,target_mhz=110,accepted_fmax_mhz=policy['accepted_fmax_mhz'],frequency_replayed=False,
        metrics_110mhz=observed,previous_metrics_110mhz=previous,fmax_gain_mhz=observed['reported_fmax_mhz']-previous['reported_fmax_mhz'],
        setup_tns_gain_ns=observed['total_negative_setup_slack_ns']-previous['total_negative_setup_slack_ns'],lut_delta_from_88_6_reference=search['lut_delta'],register_delta=search['register_delta'],
        actual_macro_input_substitutions=rewires,model_validation=model['checks'],ordinary_nets=joint['ordinary_nets'],clock_sinks=joint['clock_sinks'],carry_links=joint['carry_links'],pips_checked=search['connectivity_audit']['pips_checked'],
        bitstream_generated=True,hardware_verified=False,vendor_pnr_executed=False,open_chip_database_used=False,new_algorithm_performance_generalization_proven=False,
        complete_attempt_elapsed_seconds=elapsed,within_original_deadline=True,workspace_bytes=reserve(ROOT,0)['current_bytes'],
        source_sha256={str(f.relative_to(ROOT)):sha(f) for f in [a.attempt/'engineering_summary.json',a.attempt/'native_model_validation.json',a.guards/'native_guard_audit_002.json',checkpoint,ROOT/'results/frequency_reporting_policy_20261112.json',ROOT/'scripts/ownpr_frequency_policy.py',Path(__file__).resolve()]},
        remaining_requirements=['110 MHz complete timing closure','Native verification of an actually changed wide-leaf source candidate remains pending' if not rewires else 'No arbitrary wide-mode claim','Movable FF/clock feedback and broader hard-mode coverage remain incomplete'])
    with (a.attempt/'completion_audit.json').open('x') as stream:json.dump(record,stream,indent=2)
    print(json.dumps({k:record[k] for k in ('status','accepted_fmax_mhz','fmax_gain_mhz','setup_tns_gain_ns','actual_macro_input_substitutions','complete_attempt_elapsed_seconds','workspace_bytes','goal_complete')},indent=2))


if __name__=='__main__':main()
