"""Record native candidates, failed architectures and remaining joint-goal gates."""
import copy,json,shutil,subprocess,sys,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from ownpr_image_storage import load_json,sha
from ownpr_io_contract import audit as io_audit
from workspace_budget import reserve


def main():
    r=ROOT/'results/tmds_cofactor_joint_trial_005';budget=load_json(r/'budget.json')
    if time.time()>budget['deadline_epoch']-45:raise TimeoutError('Original deadline leaves no closeout reserve')
    capacity=reserve(ROOT,15_000_000);out=r/'closeout';out.mkdir(exist_ok=False)
    searches=['general_cofactor_search_001','cross_macro_search_002','general_costed_search_003','wide_trust_region_search_004','wide_trust_region_search_005','post_state_trust_search_012']
    search_rows=[];sources=[r/'budget.json']
    for name in searches:
        file=r/name/'manifest.json';m=load_json(file)
        if m['status'] not in ('routed','no_admissible_joint_lut_candidate') or m['returncode']:raise ValueError('Nonterminal structural search')
        sources.append(file);search_rows.append(dict(search=name,**{k:m[k] for k in ('status','trials','measured_candidates','candidate_errors','accepted_transactions')}))
    tests=['test_fixed_clock_prediction_contract.py','test_general_wide_recompile.py','test_cross_macro_recompile.py','test_wide_trust_region.py','test_existing_wide_remap.py','test_wide_selector_reorder.py','test_wide_mapping.py','test_global_timing_frontier.py','test_incremental_mode_transition.py','test_ownpr_branch_reuse.py','test_ownpr_mutable_incremental.py','test_ownpr_router.py']
    with (out/'pytest.log').open('w') as stream:
        process=subprocess.run([sys.executable,'-m','pytest','-q',*[str(ROOT/'tests'/n) for n in tests]],cwd=ROOT,stdout=stream,stderr=subprocess.STDOUT,timeout=50)
    if process.returncode:raise ValueError('Targeted tests failed')
    candidates=[]
    for name,export,placement,routes,capture in [
        ('trust004',r/'trust_export_004',r/'wide_trust_region_search_004/placement',r/'wide_trust_region_search_004',ROOT/'results/tmds_wide_ff_trial_004/clock_encoding'),
        ('state_full008',r/'state_macro_retry_008/export',r/'state_macro_place_006',r/'state_macro_retry_008/route',r/'state_macro_checkpoint_006/clock_encoding'),
        ('joint010',r/'joint_macro_checkpoint_010/export',r/'joint_macro_place_007',r/'joint_macro_checkpoint_010/route',r/'joint_macro_checkpoint_010/clock_encoding'),
        ('post012',r/'post_state_export_012',r/'post_state_trust_search_012/placement',r/'post_state_trust_search_012',r/'joint_macro_checkpoint_010/clock_encoding')]:
        em=load_json(export/'manifest.json');nm=load_json(export/'native/manifest.json');a=load_json(export/'native/handoff_audit.json');sources.extend((export/'manifest.json',export/'native/manifest.json',export/'native/handoff_audit.json'))
        if em['status']!='verified_handoff_complete' or not em['sta_completed'] or not em['bitstream_generated'] or nm['vendor_placement_executed'] or nm['vendor_routing_executed']:raise ValueError('Incomplete native candidate')
        if nm['packed_sha256']!=sha(placement/'packed.json') or nm['placement_sha256']!=sha(placement/'placement.json') or nm['routes_sha256']!=sha(routes/'routes.json'):raise ValueError('Candidate provenance changed')
        contract=io_audit(load_json(placement/'packed.json')['modules']['top'],(ROOT/'results/tangprimer138_suite_001/tmds/fixed.cst').read_text(),(ROOT/'results/tmds_route_coupled_trial_004/provisional_evaluation.sdc').read_text())
        t=a['timing'];eligible=bool(em['metrics']['other_checks_passed'] and not t['hold_violated_endpoints'] and t['async_constraints_passed'] and t['minimum_pulse_width_passed'])
        if contract['ports']!=23 or a['io']['ports']!=23 or not a['io']['pins_preserved'] or not a['io']['electrical_report_matched'] or a['frame_integrity']['status']!='frame_integrity_verified':raise ValueError('IO/configuration regression')
        if t['path_coverage']['setup']['paths']!=66 or t['path_coverage']['hold']['paths']!=66 or t['pulse_width_checks']!=76:raise ValueError('Changed complete timing coverage')
        if sha(placement/'packed.json')!=sha(ROOT/'results/tmds_route_coupled_trial_004/existing_wide_batch_search_002/placement/packed.json'):raise ValueError('Unexpected functional netlist change in physical pilot')
        candidates.append(dict(name=name,metrics=em['metrics'],non_setup_native_checks_passed=eligible,selected=dict(placement=placement,routes=routes,wire=export/'wire',sta=export/'native',clock_capture=capture),full_bitstream_verified=False))
    selected=max((c for c in candidates if c['non_setup_native_checks_passed']),key=lambda c:c['metrics']['reported_fmax_mhz'])
    if selected['name'] not in ('joint010','post012'):raise ValueError('New joint candidates failed to improve incumbent')
    fresh=r/('post_state_bidir_model_012' if selected['name']=='post012' else 'joint_bidir_model_010')
    fm=load_json(fresh/'manifest.json')
    if fm['status']!='validated_setup_hold_feedback' or fm['model_sha256']!=sha(fresh/'model.json'):raise ValueError('Missing fresh native-calibrated selected model')
    sources.extend((fresh/'manifest.json',fresh/'model.json'))
    if selected['name']=='post012':
        for filename,status in [('post_state_endpoint_audit_012.json','changed_netlist_endpoint_envelopes_validated'),('post_state_full_graph_audit_012.json','full_native_data_move_prediction_validated')]:
            file=r/filename
            if load_json(file)['status']!=status:raise ValueError('Selected feedback prediction was not independently validated')
            sources.append(file)
    for family in ('general','cross'):
        file=r/('generalization_'+family+'_0'+('09' if family=='general' else '11'))/'manifest.json'
        gm=load_json(file)
        if gm['status']!='structural_generalization_checked_not_performance' or len(gm['cases'])!=4:raise ValueError('Generalization checks incomplete')
        for case in gm['cases']:
            if case['transformations'] and not all(p['cec']['equivalent'] for p in case['cec']):raise ValueError('Cross-design CEC failed')
        sources.append(file)
    # Keep the old-to-new proof chain explicit. Placement manifests inherited
    # older proof fields; current byte identity is independently checked above.
    proof_parent=ROOT/'results/tmds_route_coupled_trial_004/existing_wide_batch_search_002/manifest.json'
    parent=load_json(proof_parent)
    if not all(p['cec']['equivalent'] for p in parent['proof']['checks']):raise ValueError('Missing upstream functional proof')
    state=selected['selected'];previous=load_json(r/'joint_reference_010.json')['designs'][0]
    row=dict(design=previous['design'],constraints=copy.deepcopy(previous['constraints']))
    row.update(selected={k:v.relative_to(ROOT).as_posix() for k,v in state.items()},metrics=selected['metrics'],qualified_mhz=None,goal_complete=False,
        native_reported_candidate_only=True,fair_official_baseline_frozen=False,competitive_ratio=None,sta_evaluation_mhz=166.998888,
        target_mhz=None,target_frequency_policy='1.20 times fair official baseline; evaluation1.224 times baseline; fair baseline pending',
        frequency_acceptance_basis='native_reported_fmax_with_full_IO_hold_async_pulse_checks; broader proof/configuration gates remain',
        setup_closed_at_evaluation_constraint=False,full_bitstream_verified=False,new_algorithm_performance_generalization_proven=False,
        functional_lineage=dict(byte_identical_to_parent=True,parent_packed_sha256=parent['netlist_sha256'],upstream_fixed_state_cec_manifest=proof_parent.relative_to(ROOT).as_posix(),inherited_placement_proof_fields_are_not_a_new_proof=True))
    files=[state['placement']/'packed.json',state['placement']/'placement.json',state['routes']/'routes.json',state['routes']/'nets.json',state['routes']/'all_nets.json',state['routes']/'clock_tree.json',state['wire']/'wire_delays.json',state['sta']/'manifest.json',state['sta']/'handoff_audit.json',state['sta']/'probe.tr',ROOT/row['constraints']['cst'],ROOT/row['constraints']['sdc']]
    row['selected_source_sha256']={f.relative_to(ROOT).as_posix():sha(f) for f in files}
    (r/'reported_fmax_checkpoint.json').write_text(json.dumps(dict(designs=[row],goal_complete=False),indent=2))
    combined=load_json(ROOT/'results/vexriscv_shared_dag_trial_001/all_designs_reported_fmax_checkpoint.json');vex=load_json(ROOT/'results/vexriscv_wide_joint_trial_001/reported_fmax_checkpoint.json')['designs'][0]
    combined['designs']=[vex if x['design']=='vexriscv' else row if x['design']=='tmds' else x for x in combined['designs']]
    combined.update(goal_complete=False,fair_official_baselines_frozen=False,minimum_competitive_ratio=None,new_algorithm_performance_generalization_proven=False)
    (r/'all_designs_reported_fmax_checkpoint.json').write_text(json.dumps(combined,indent=2))
    snapshots=out/'sources';snapshots.mkdir()
    names=['ownpr_general_wide_recompile.py','ownpr_cross_macro_recompile.py','ownpr_wide_trust_region.py','ownpr_existing_wide_remap.py','ownpr_joint_lut_search.py','ownpr_incremental_routes.py','run_ownpr_timing_route.py','register_control_map.py','evaluate_joint_lut_graph.py']
    for name in names:shutil.copyfile(ROOT/'scripts'/name,snapshots/name)
    for name in ('prepare_state_macro_trust_region.py','check_existing_wide_generalization.py','close_cofactor_joint_trial.py'):shutil.copyfile(ROOT/'experiments'/name,snapshots/name)
    sources.extend([proof_parent,r/'vex_unused_pad_inventory.json',r/'worst_path_comparison.json',r/'reported_fmax_checkpoint.json',r/'all_designs_reported_fmax_checkpoint.json',out/'pytest.log',*snapshots.iterdir()])
    elapsed=time.time()-budget['start_epoch']
    record=dict(status='bounded_joint_attempt_completed_goal_unfinished',original_budget=budget,elapsed_seconds=elapsed,within_original_deadline=time.time()<budget['deadline_epoch'],
        selected_candidate=selected['name'],native_reported_fmax_mhz=selected['metrics']['reported_fmax_mhz'],gain_over_previous112_368=selected['metrics']['reported_fmax_mhz']/112.368-1,
        candidates=candidates,searches=search_rows,goal_complete=False,fair_official_baselines_frozen=False,minimum_competitive_ratio=None,
        source_functional_netlist_byte_identical=True,hardware_verified=False,full_bitstream_verified=False,performance_generalization_proved=False,
        pending=['Four fair official baselines including official unused Vex ERR physical pad defaults and unbounded SDPB/MULTALU proof','Cross-design routed performance of the new algorithm','All four designs >=1.20 times their fair official baseline','Broader source/physical-mode and complete configuration semantics'],
        workspace_before=capacity,workspace_after=reserve(ROOT,0),source_sha256={str(f.resolve()):sha(f) for f in sources})
    if not record['within_original_deadline']:raise TimeoutError('Original attempt deadline exceeded')
    (r/'completion_audit.json').write_text(json.dumps(record,indent=2,default=str));print(json.dumps({k:record[k] for k in ('status','elapsed_seconds','selected_candidate','native_reported_fmax_mhz','gain_over_previous112_368','within_original_deadline')},indent=2))


if __name__=='__main__':main()
