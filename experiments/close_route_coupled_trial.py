"""Close the original bounded trial, retaining failures and partial status."""
import copy,json,shutil,subprocess,sys,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from ownpr_image_storage import load_json,sha
from workspace_budget import reserve
from ownpr_io_contract import audit as io_audit
from audit_fixed_clock_model_prediction import audit


def main():
    trial=ROOT/'results/tmds_route_coupled_trial_004';budget=load_json(trial/'budget.json')
    if time.time()>budget['deadline_epoch']-45:raise TimeoutError('Original deadline leaves no closeout reserve')
    capacity=reserve(ROOT,25_000_000);out=trial/'closeout';out.mkdir(exist_ok=False)
    source=ROOT/'results/tmds_112_3mhz_001/export/native';native=trial/'selected_export_002/native'
    old_sdc=ROOT/'results/tmds_112_3mhz_001/clock.sdc';new_sdc=trial/'provisional_evaluation.sdc'
    args=(trial/'incumbent_bidir_model',trial/'independent_rebuild_002',trial/'selected_bidir_model_002',old_sdc,new_sdc,source,native)
    positive=audit(*args);(out/'full_prediction_audit.json').write_text(json.dumps(positive,indent=2))
    controls=[]
    for kind in ('wire_delay','endpoint_requirement','clock_falling_seed'):
        folder=out/('negative_'+kind);folder.mkdir();model=load_json(args[1]/'model.json');meta=load_json(args[1]/'manifest.json')
        if kind=='wire_delay':next(e for e in model['late']['edges'] if e['kind']=='wire')['delay'][0]+=.025
        elif kind=='endpoint_requirement':next(iter(model['setup_requirements'].values()))[0]+=.025
        else:model['late']['seeds'][positive['falling_primary_clock_seed']][1]+=.025
        (folder/'model.json').write_text(json.dumps(model,separators=(',',':')));meta['model_sha256']=sha(folder/'model.json')
        (folder/'manifest.json').write_text(json.dumps(meta,indent=2))
        try:audit(args[0],folder,*args[2:])
        except ValueError as error:controls.append(dict(kind=kind,status='rejected',reason=str(error)))
        else:raise ValueError('Unsound prediction acceptance: '+kind)
    tests=('test_fixed_clock_prediction_contract.py','test_existing_wide_remap.py','test_wide_selector_reorder.py','test_wide_mapping.py','test_global_timing_frontier.py')
    with (out/'pytest.log').open('w') as stream:
        process=subprocess.run([sys.executable,'-m','pytest','-q',*[str(ROOT/'tests'/n) for n in tests]],cwd=ROOT,stdout=stream,stderr=subprocess.STDOUT,timeout=40)
    if process.returncode:raise ValueError('Targeted tests failed')
    export=load_json(trial/'selected_export_002/manifest.json');handoff=load_json(native/'handoff_audit.json');search=load_json(trial/'existing_wide_batch_search_002/manifest.json')
    placement=trial/'existing_wide_batch_search_002/placement';design=load_json(placement/'packed.json')
    contract=io_audit(design['modules']['top'],(ROOT/'results/tangprimer138_suite_001/tmds/fixed.cst').read_text(),new_sdc.read_text())
    if export['status']!='verified_handoff_complete' or not export['sta_completed'] or not export['bitstream_generated']:raise ValueError('Incomplete final native export')
    if search['candidate_errors'] or not search['proof']['checks'] or not all(x['cec']['equivalent'] for x in search['proof']['checks']):raise ValueError('Missing changed-netlist CEC')
    if not search['connectivity_audit']['no_node_conflicts'] or not handoff['io']['pins_preserved'] or not handoff['io']['electrical_report_matched']:raise ValueError('Physical/IO regression')
    timing=handoff['timing']
    if timing['hold_violated_endpoints'] or not timing['async_constraints_passed'] or not timing['minimum_pulse_width_passed']:raise ValueError('Non-setup timing gate failed')
    if timing['path_coverage']['setup']['paths']!=66 or timing['path_coverage']['hold']['paths']!=66:raise ValueError('Missing native endpoint coverage')
    gen=load_json(trial/'structural_generalization_001/manifest.json')
    if gen['status']!='structural_generalization_checked_not_performance' or len(gen['cases'])!=4:raise ValueError('Missing structural generalization')
    snapshots=out/'sources';snapshots.mkdir()
    scripts=['scripts/audit_fixed_clock_model_prediction.py','scripts/evaluate_joint_lut_graph.py','scripts/ownpr_model_reference.py','scripts/ownpr_bidir_timing.py','experiments/close_route_coupled_trial.py','experiments/check_existing_wide_generalization.py']
    for name in scripts:shutil.copyfile(ROOT/name,snapshots/Path(name).name)
    for name in ('ownpr_existing_wide_remap.py','ownpr_wide_mapping_feedback.py','reorder_ownpr_wide_inputs.py'):
        frozen=trial/'existing_wide_batch_search_002/sources'/name
        if sha(ROOT/'scripts'/name)!=sha(frozen):raise ValueError('Generalization implementation changed since routed candidate')
        shutil.copyfile(frozen,snapshots/name)
    metrics=export['metrics'];elapsed=time.time()-budget['start_epoch']
    record=dict(status='bounded_attempt_completed_goal_unfinished',original_budget=budget,elapsed_seconds=elapsed,within_original_deadline=time.time()<budget['deadline_epoch'],
        reported_candidate_fmax_mhz=metrics['reported_fmax_mhz'],previous_native_fmax_mhz=112.368,relative_native_gain=metrics['reported_fmax_mhz']/112.368-1,
        sta_evaluation_mhz=1000/contract['period_ns'],metrics=metrics,named_io_contract=contract,negative_prediction_controls=controls,
        native_reported_candidate_only=True,full_bitstream_verified=False,hardware_verified=False,goal_complete=False,
        fair_official_baseline_frozen=False,competitive_ratio=None,new_algorithm_performance_generalization_proved=False,
        structural_generalization=[dict(design=x['design'],status=x['status'],transformed_macros=len(x['transformations'])) for x in gen['cases']],
        vendor_placement_executed=False,vendor_routing_executed=False,open_chip_database_used=False,
        pending=['Four fair official baselines with complete provenance/hold/equivalence/physical IO gates','Vex unused ERR pad realization and SDPB/MULTALU unbounded equivalence','Broader configuration/physical-mode coverage and bitstream semantics','Cross-design complete native STA after the new structural algorithm','Each design >=1.20 times its fair frozen official baseline'],
        capacity_before=capacity,capacity_after=reserve(ROOT,0),
        source_sha256={str(f.resolve()):sha(f) for f in [trial/'budget.json',trial/'existing_wide_batch_search_002/manifest.json',trial/'selected_export_002/manifest.json',native/'handoff_audit.json',trial/'structural_generalization_001/manifest.json',out/'full_prediction_audit.json',out/'pytest.log',*snapshots.iterdir()]})
    if not record['within_original_deadline']:raise TimeoutError('Trial closeout exceeded original deadline')
    (trial/'completion_audit.json').write_text(json.dumps(record,indent=2));print(json.dumps({k:record[k] for k in ('status','elapsed_seconds','reported_candidate_fmax_mhz','relative_native_gain','sta_evaluation_mhz','within_original_deadline')},indent=2))


if __name__=='__main__':main()
