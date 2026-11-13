"""Audit the bounded clock/data trial without claiming the joint goal complete."""
import copy,json,shutil,subprocess,sys,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from ownpr_image_storage import load_json,sha
from ownpr_model_reference import validate_reconstruction,load_reference
from ownpr_io_contract import audit as io_audit
from workspace_budget import reserve


def main():
    r=ROOT/'results/clock_data_cooptimization_trial_007';budget=load_json(r/'budget.json')
    if time.time()+120>budget['deadline_epoch']:raise TimeoutError('Insufficient original closeout budget')
    storage=reserve(ROOT,20000000);out=r/'closeout';out.mkdir(exist_ok=False)
    tests=['test_critical_cover.py','test_clock_data_model.py','test_shared_macro_feedback.py','test_shared_dag.py','test_ownpr_clock_timing_search.py','test_ownpr_clock_configuration.py','test_ownpr_clock_slack.py','test_bidir_timing.py','test_wide_trust_region.py','test_ownpr_branch_reuse.py','test_ownpr_mutable_incremental.py']
    with (out/'pytest.log').open('w') as stream:
        code=subprocess.run([sys.executable,'-m','pytest','-q',*[str(ROOT/'tests'/n) for n in tests]],cwd=ROOT,stdout=stream,stderr=subprocess.STDOUT,timeout=65).returncode
    if code:raise ValueError('Focused verification failed')
    placement=r/'clock_data_trust_search_011/placement';routes=r/'clock_data_trust_search_011';wire=r/'clock_data_export_013/wire';native=r/'clock_data_export_retry_014/native';model=r/'final_bidir_model_019'
    source=ROOT/'results/tmds_cofactor_joint_trial_005/reported_fmax_checkpoint.json';previous=load_json(source)['designs'][0]
    for path,h in previous['selected_source_sha256'].items():
        if sha(ROOT/path)!=h:raise ValueError('Source checkpoint changed')
    if sha(placement/'packed.json')!=sha(ROOT/previous['selected']['placement']/'packed.json'):raise ValueError('Changed functional design')
    old=load_json(ROOT/previous['selected']['placement']/'placement.json');new=load_json(placement/'placement.json')
    oldffs={x['ff']:x for x in old['clusters'].values() if x.get('ff')};newffs={x['ff']:x for x in new['clusters'].values() if x.get('ff')}
    if oldffs!=newffs or old['fixed_io']!=new['fixed_io']:raise ValueError('Changed state/fixed IO placement')
    _,_,actual=load_reference(model,placement,routes,wire);predicted=load_json(routes/'feedback/model.json')
    for mode in ('setup','hold'):
        if actual[mode+'_requirements']!=predicted[mode+'_requirements']:raise ValueError('Fixed-clock requirements changed')
    graphs=lambda m:dict(setup=m['late'],hold=m['early'])
    checks=validate_reconstruction(actual,graphs(actual),graphs(predicted),.002)
    negative=[]
    for label in ('late_arc','late_launch','early_launch'):
        bad=copy.deepcopy(predicted)
        if label=='late_arc':bad['late']['edges'][0]['delay'][0]+=.025
        else:
            key='early' if label=='early_launch' else 'late';seed=next(n for n in bad[key]['seeds'] if n.endswith('/Q'));bad[key]['seeds'][seed][0]+=.025
        try:validate_reconstruction(actual,graphs(actual),graphs(bad),.002)
        except ValueError as e:negative.append(dict(mutation=label,rejected=True,reason=str(e)))
        else:raise ValueError('Real timing prediction mutation survived: '+label)
    proof=dict(status='full_native_data_move_prediction_validated',checks=checks,negative_controls=negative,source_sha256={str(f.resolve()):sha(f) for f in (model/'model.json',model/'manifest.json',routes/'feedback/model.json',placement/'packed.json',placement/'placement.json',native/'manifest.json',Path(__file__))})
    (r/'final_prediction_audit_022.json').write_text(json.dumps(proof,indent=2))
    em=load_json(r/'clock_data_export_retry_014/manifest.json');nm=load_json(native/'manifest.json');a=load_json(native/'handoff_audit.json');t=a['timing']
    if em['status']!='verified_handoff_complete' or not em['sta_completed'] or not em['bitstream_generated'] or nm['vendor_placement_executed'] or nm['vendor_routing_executed']:raise ValueError('Incomplete own handoff')
    if any(nm[k]!=sha(f) for k,f in [('packed_sha256',placement/'packed.json'),('placement_sha256',placement/'placement.json'),('routes_sha256',routes/'routes.json'),('clock_tree_sha256',routes/'clock_tree.json')]):raise ValueError('Native source mismatch')
    if not em['metrics']['other_checks_passed'] or t['hold_violated_endpoints'] or not t['async_constraints_passed'] or not t['minimum_pulse_width_passed']:raise ValueError('Native non-setup failure')
    if {k:v['paths'] for k,v in t['path_coverage'].items()}!=dict(setup=66,hold=66,recovery=0,removal=0) or t['pulse_width_checks']!=76:raise ValueError('Changed native coverage')
    contract=io_audit(load_json(placement/'packed.json')['modules']['top'],(ROOT/previous['constraints']['cst']).read_text(),(ROOT/previous['constraints']['sdc']).read_text())
    if contract['ports']!=23 or a['io']['ports']!=23 or not a['io']['pins_preserved'] or not a['io']['electrical_report_matched'] or a['frame_integrity']['status']!='frame_integrity_verified':raise ValueError('IO/configuration gate failed')
    clockcheck=load_json(r/'full_graph_prediction_010.json')
    if clockcheck['status']!='complete_native_clock_delta_prediction_validated':raise ValueError('Clock-delta admission incomplete')
    guards=load_json(r/'copy_generalization_020/manifest.json')
    if len(guards['cases'])!=4 or any(x['status']!='exact_copy_operator_cec_passed' for x in guards['cases']):raise ValueError('Copy structural guards incomplete')
    row=copy.deepcopy(previous);state=dict(placement=placement,routes=routes,wire=wire,sta=native,clock_capture=r/'clock_encoding_003')
    row.update(selected={k:v.relative_to(ROOT).as_posix() for k,v in state.items()},metrics=em['metrics'],qualified_mhz=None,competitive_ratio=None,goal_complete=False,
        fair_official_baseline_frozen=False,native_reported_candidate_only=True,setup_closed_at_evaluation_constraint=False,new_algorithm_performance_generalization_proven=False,
        validated_bidir_model=model.relative_to(ROOT).as_posix(),source_checkpoint=source.relative_to(ROOT).as_posix(),
        functional_lineage=dict(byte_identical_to_parent=True,parent_packed_sha256=sha(placement/'packed.json'),upstream_fixed_state_cec_manifest=previous['functional_lineage']['upstream_fixed_state_cec_manifest'],no_new_source_or_hard_state_proof_claimed=True))
    files=[placement/'packed.json',placement/'placement.json',routes/'routes.json',routes/'nets.json',routes/'all_nets.json',routes/'clock_tree.json',wire/'wire_delays.json',native/'manifest.json',native/'handoff_audit.json',native/'probe.tr',ROOT/previous['constraints']['cst'],ROOT/previous['constraints']['sdc'],model/'model.json',model/'manifest.json',r/'full_graph_prediction_010.json',r/'final_prediction_audit_022.json',r/'clock_encoding_003/encoding_audit.json']
    row['selected_source_sha256']={f.relative_to(ROOT).as_posix():sha(f) for f in files}
    (r/'reported_fmax_checkpoint.json').write_text(json.dumps(dict(designs=[row],goal_complete=False),indent=2))
    combined=load_json(ROOT/'results/tmds_cofactor_joint_trial_005/all_designs_reported_fmax_checkpoint.json')
    combined['designs']=[row if x['design']=='tmds' else x for x in combined['designs']]
    for x in combined['designs']:
        x['historical_packet_qualified_mhz']=x.get('qualified_mhz');x['qualified_mhz']=None;x['qualified']=False;x['fair_official_baseline_frozen']=False;x['competitive_ratio']=None
        x['target_mhz']=None;x['goal_complete']=False;x['joint_goal_acceptance_pending']=True
    combined.update(goal_complete=False,fair_official_baselines_frozen=False,minimum_competitive_ratio=None,new_algorithm_performance_generalization_proven=False)
    (r/'all_designs_reported_fmax_checkpoint.json').write_text(json.dumps(combined,indent=2))
    snapshots=out/'sources';snapshots.mkdir()
    sources=['ownpr_clock_data_model.py','ownpr_full_clock_search_model.py','ownpr_ideal_skew_bound.py','audit_clock_data_prediction.py','ownpr_critical_cover.py','ownpr_joint_lut_search.py','ownpr_shared_feedback.py','ownpr_model_reference.py','ownpr_wide_trust_region.py']
    for n in sources:shutil.copyfile(ROOT/'scripts'/n,snapshots/n)
    for n in ('run_full_clock_data_search.py','clock_data_opportunity.py','check_critical_copy_generalization.py','close_clock_data_trial.py'):shutil.copyfile(ROOT/'experiments'/n,snapshots/n)
    files.extend([source,r/'reported_fmax_checkpoint.json',r/'all_designs_reported_fmax_checkpoint.json',out/'pytest.log',*snapshots.iterdir()])
    for folder in ('critical_cover_search_015','critical_cover_retry_018','critical_cover_selector_021','copy_generalization_020','native_export_005','clock_data_export_013','clock_data_export_retry_014'):files.append(r/folder/'manifest.json')
    result=dict(status='bounded_clock_data_attempt_completed_goal_unfinished',original_budget=budget,elapsed_seconds=time.time()-budget['start_epoch'],within_original_deadline=time.time()<budget['deadline_epoch'],
        previous_goal_turn='no_progress_text_only_goal_edit_request; terminal search002 was revalidated before continuation',current_goal_turn='progress',native_reported_fmax_mhz=em['metrics']['reported_fmax_mhz'],
        gain_over_source=em['metrics']['reported_fmax_mhz']/previous['metrics']['reported_fmax_mhz']-1,metrics=em['metrics'],full_prediction_validation=proof,logical_netlist_byte_identical=True,
        copy_operator_four_design_cec_passed=True,critical_cover_performance_generalization_proven=False,
        rejected_cover_candidate_reason='Retry018 improves only the earlier122.940MHz model by0.016875ns and is worse than the final123.865MHz incumbent; no native Fmax promoted. Selector021 offers no finite covering set.',
        retained_failures=['Optional export013 alias-only timing feedback API rejects macro movement; full native retry014 reused exact measured wires, same original deadline.','Cover015 completed measurements but failed audit serialization; corrected retry018 retains original failed evidence.'],
        goal_complete=False,fair_official_baselines_frozen=False,minimum_competitive_ratio=None,full_bitstream_verified=False,hardware_programmed=False,
        pending=['Four verified fair official references, including Vex unused ERR pad defaults and unbounded memory/DSP state proof','Broader physical/configuration-mode semantics','All four >=1.20 ratios using one flow; routed performance generalization of new operators'],
        workspace_before=storage,workspace_after=reserve(ROOT,0),source_sha256={str(f.resolve()):sha(f) for f in files})
    if not result['within_original_deadline']:raise TimeoutError('Original attempt deadline exceeded')
    (r/'completion_audit.json').write_text(json.dumps(result,indent=2));print(json.dumps({k:result[k] for k in ('status','elapsed_seconds','native_reported_fmax_mhz','gain_over_source','within_original_deadline')},indent=2))


if __name__=='__main__':main()
