"""Close the original bounded attempt; reject regressions and preserve source."""
import copy,json,sys,time,subprocess,shutil
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from ownpr_image_storage import load_json,sha
from workspace_budget import reserve


def main():
    r=ROOT/'results/heterogeneous_choice_joint_trial_008';budget=load_json(r/'budget.json');reserve(ROOT,10000000)
    if time.time()+90>budget['deadline_epoch']:raise TimeoutError('Original attempt closeout reserve')
    out=r/'closeout';out.mkdir(exist_ok=False)
    tests=['test_heterogeneous_timing.py','test_hold_aware_pins.py','test_shared_ff_packing.py','test_ownpr_shared_ff_calibration.py','test_critical_cover.py','test_clock_data_model.py','test_shared_macro_feedback.py','test_shared_dag.py','test_ownpr_clock_timing_search.py','test_ownpr_clock_configuration.py','test_ownpr_clock_slack.py','test_bidir_timing.py','test_wide_trust_region.py','test_ownpr_branch_reuse.py','test_ownpr_mutable_incremental.py','test_incremental_mode_transition.py']
    with (out/'pytest.log').open('w') as stream:
        code=subprocess.run([sys.executable,'-m','pytest','-q',*[str(ROOT/'tests'/x) for x in tests]],cwd=ROOT,stdout=stream,stderr=subprocess.STDOUT,timeout=60).returncode
    if code:raise ValueError('Focused tests failed')
    checkpoint=ROOT/'results/clock_data_cooptimization_trial_007/reported_fmax_checkpoint.json';selected=load_json(checkpoint)['designs'][0]
    for path,h in selected['selected_source_sha256'].items():
        if sha(ROOT/path)!=h:raise ValueError('Incumbent changed')
    cases=[];sources=[checkpoint,r/'budget.json',out/'pytest.log',r/'native_path_comparison_012.json',r/'source_archive_audit_013.json']
    for label,export in [('mapped_fixed_state',r/'tmds_joint_checkpoint_008/export'),('mapped_joint_wide_ff',r/'tmds_joint_register_export_retry_011')]:
        m=load_json(export/'manifest.json');native=export/'native';n=load_json(native/'manifest.json');a=load_json(native/'handoff_audit.json');t=a['timing']
        if m['status']!='verified_handoff_complete' or not m['sta_completed'] or not m['bitstream_generated'] or n['vendor_placement_executed'] or n['vendor_routing_executed']:raise ValueError('Incomplete own implementation')
        for key,filename in [('cst_sha256','cst'),('source_sdc_sha256','sdc')]:
            if n[key]!=sha(ROOT/selected['constraints'][filename]):raise ValueError('Changed final constraints')
        if a['io']['ports']!=23 or not a['io']['pins_preserved'] or not a['io']['electrical_report_matched'] or a['frame_integrity']['status']!='frame_integrity_verified':raise ValueError('Changed IO/configuration envelope')
        if {k:v['paths'] for k,v in t['path_coverage'].items()}!=dict(setup=66,hold=66,recovery=0,removal=0) or t['pulse_width_checks']!=76:raise ValueError('Timing coverage omission')
        cases.append(dict(candidate=label,metrics=m['metrics'],adopted=False,hold_passed=not t['hold_violated_endpoints'],reported_fmax_eligible_for_recognition=not t['hold_violated_endpoints'] and m['metrics']['other_checks_passed'],
            rejection='Below unchanged123.865MHz incumbent' if not t['hold_violated_endpoints'] else 'Hold failure; raw reported Fmax is not accepted',full_bitstream_verified=False))
        sources.extend([export/'manifest.json',native/'manifest.json',native/'handoff_audit.json',native/'probe.tr'])
    mapping=load_json(r/'tmds_bridge_pin_retry_006/manifest.json')
    if any(c['status']!='mapped_source_cec_passed' or not all(p['cec']['equivalent'] for p in c['proof']) for c in mapping['candidates']):raise ValueError('Source CEC incomplete')
    packing=load_json(r/'tmds_joint_register_place_009/manifest.json')
    if len(packing['joint_register_boundary_changes'])!=9 or not all(p['cec']['equivalent'] for p in packing['proof']):raise ValueError('Joint wide-register proof incomplete')
    actual=load_json(r/'tmds_joint_register_place_009/packed.json');layout=load_json(r/'tmds_joint_register_place_009/placement.json')
    inventory=dict(actual_cells=len(actual['modules']['top']['cells']),actual_lut_clusters=len(layout['clusters']),removed_identity_luts=9,shared_pairs=packing['shared_control_pair_plan']['shared_pairs'] if packing['shared_control_pair_plan'] else 0,
        inherited_prepack_counts_are_not_postpack_measurements=True,clock_replanned_and_native_checked=True)
    # Keep the winning earlier implementation, never attribute its frequency
    # to this failed new algorithm or fill an unverified competitive ratio.
    combined=load_json(ROOT/'results/clock_data_cooptimization_trial_007/all_designs_reported_fmax_checkpoint.json');combined.update(selected_native_implementations_unchanged=True,new_algorithm_adopted=False,goal_complete=False,minimum_competitive_ratio=None)
    (r/'all_designs_reported_fmax_checkpoint.json').write_text(json.dumps(combined,indent=2))
    sources.extend([r/'tmds_bridge_pin_retry_006/manifest.json',r/'tmds_joint_register_place_009/manifest.json',r/'all_designs_reported_fmax_checkpoint.json',ROOT/'carbon-advice-20261112-heterogeneous-choice-32.md'])
    shutil.copyfile(__file__,r/'closeout_sources'/Path(__file__).name)
    sources.extend((r/'closeout_sources').iterdir())
    result=dict(status='bounded_heterogeneous_trial_completed_without_gain',previous_goal_turn='progress',current_goal_turn='progress',original_budget=budget,elapsed_seconds=time.time()-budget['start_epoch'],within_original_deadline=time.time()<budget['deadline_epoch'],
        cases=cases,actual_joint_packing_inventory=inventory,selected_tmds_native_fmax_mhz_unchanged=123.865,selected_vex_native_fmax_mhz_unchanged=90.380,
        complete_mapped_retention_states_explored=24,new_algorithm_performance_generalization_proven=False,
        source_matching='Original source and selected soft reference pass fixed-state hard-boundary CEC before budget transport; each final candidate passes actual-JSON source CEC',
        retained_failures=['001 relative ABC path','002 unsupported native mux slot0 for LUT6','005 propagated inactive transition sentinel rejected; replaced numerical inference with active-transition provenance','010 export preflight reservation stopped before writing; lossless cleanup and export-only retry011 use original budget'],
        prototype_limits=['Unplaced wires are an explicit prior, not native timing','Only calibrated ALU mode2; other modes reject','New whole-model Vex test rejects undriven DSP CLK bit284; no zero-arrival/clock fallback','No new UART/int2float performance application in this attempt','Budget iterations are bounded and not proven converged','Earlier prototype source hashes remain, not every transient version has retained bytes; candidate006 engine snapshot is recovered only after exact hash match'],
        goal_complete=False,fair_official_baselines_frozen=False,minimum_competitive_ratio=None,pending=['Competitive fair baselines incl Vex pad/default-electrical and unbounded RAM/DSP source proof','All four >=1.20 individual ratios','Mapper/physical feedback convergence, loaded interconnect and joint packing-aware cut costs','Broader primitive/mode/configuration verification'],
        workspace=reserve(ROOT,0),source_sha256={str(f.resolve()):sha(f) for f in sources})
    if not result['within_original_deadline']:raise TimeoutError('Original attempt deadline exceeded')
    (r/'completion_audit.json').write_text(json.dumps(result,indent=2));print(json.dumps({k:result[k] for k in ('status','elapsed_seconds','within_original_deadline','selected_tmds_native_fmax_mhz_unchanged')},indent=2))


if __name__=='__main__':main()
