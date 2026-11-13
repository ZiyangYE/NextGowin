"""Close trial003 against native evidence; never freeze incomplete baselines."""
import json,re,time,shutil
from pathlib import Path
from ownpr_image_storage import load_json,read_text,sha
from workspace_budget import reserve
from project_timing import parse,section
ROOT=Path(__file__).resolve().parents[1]


def main():
    reserve(ROOT,5_000_000);out=ROOT/'results/official_baseline_contract_trial_003';budget=load_json(out/'budget.json')
    cat=load_json(ROOT/'results/official_joint_baseline_scout_001/target_probe_catalog.json');entries={e['design']:e for e in cat['designs']}
    artifacts={};rows=[];selected={}
    def retain(p):artifacts[str(p.relative_to(ROOT))]=sha(p)
    for folder,count in [('portfolio',12),('portfolio_low',8)]:
        cohort=load_json(out/folder/'manifest.json')
        assert cohort['status']=='portfolio_completed_no_frozen_baselines' and cohort['within_original_deadline'] and cohort['original_budget']==budget and len(cohort['cases'])==count
        retain(out/folder/'manifest.json')
        for row in cohort['cases']:
            p=out/folder/row['case'];meta=load_json(p/'manifest.json');r=load_json(p/'reload_result.json')
            assert meta['returncode']==0 and meta['within_original_deadline'] and r['status']=='fixed_layout_sta_calibrated'
            e=entries[row['design']]
            assert sha(p/'probe.vg')==sha(ROOT/e['netlist']) and sha(p/'project.cst')==sha(ROOT/e['cst'])
            assert row['result_sha256']==sha(p/'reload_result.json') and all(x['physical_state_unchanged'] for x in r['phases'])
            assert max(r['same_period_maximum_envelope_error_ns'].values())==0
            assert all(max(x['maximum_errors_ns'].values())<=.0011 for x in r['changed_period_invariants'].values())
            record={k:row[k] for k in ('design','case','implementation_mhz','reported_fmax_mhz','timing_eligible','hold_passed','other_checks_passed')};record['folder']=folder;rows.append(record)
            if record['timing_eligible']:
                old=selected.get(record['design'])
                if old is None or (record['reported_fmax_mhz'],record['implementation_mhz'])>(old['reported_fmax_mhz'],old['implementation_mhz']):selected[record['design']]=record
            retain(p/'manifest.json');retain(p/'reload_result.json')
    assert set(selected)==set(entries)
    io=load_json(out/'complete_named_io_contracts.json');assert len(io['designs'])==4 and all(r['status']=='complete_named_pin_and_zero_io_budget_coverage' for r in io['designs']);retain(out/'complete_named_io_contracts.json')
    positive=load_json(out/'sdpb_array_sdk_validation_final/manifest.json');assert positive['status']=='sdk_array_differential_simulation_passed' and positive['configurations']==24 and positive['stimulus_steps']==4096
    retain(out/'sdpb_array_sdk_validation_final/manifest.json')
    for mutation in ('address','clock','init','read_mode','reset_mode','byte_enable'):
        p=out/('sdpb_negative2_'+mutation);r=load_json(p/'manifest.json')
        assert r['status']=='negative_control_rejected' and r['compile_returncode']==0 and r['simulate_returncode']!=0 and 'MISMATCH' in (p/'simulate.log').read_text()
        retain(p/'manifest.json');retain(p/'simulate.log')
    model=load_json(out/'vex_memory_model_003/manifest.json');assert model['status']=='array_models_built_not_proved' and model['equivalence_proved'] is False and model['memory_counts']=={'gold':8,'gate':10}
    retain(out/'vex_memory_model_003/manifest.json');retain(out/'vex_memory_model_003/memory_inventory.json')
    checkpoint=out/'tmds_path_checkpoint_001';c=load_json(checkpoint/'manifest.json');assert c['status']=='checkpoint_handoff_complete' and c['wall_seconds']<=600
    assert all(s['status']=='passed' for s in c['stages']) and c['metrics']['reported_fmax_mhz']==84.842 and c['metrics']['hold_violations']==0
    native=checkpoint/'export/native';export=load_json(checkpoint/'export/manifest.json');assert export['status']=='verified_handoff_complete' and export['sta_completed'] and export['bitstream_generated']
    assert sha(out/'tmds_path_global_003/packed.json')==sha(ROOT/'results/tmds_wide_ff_hold_002/round_000/placement/packed.json')
    for p in (checkpoint/'manifest.json',checkpoint/'export/manifest.json',out/'tmds_path_global_003/manifest.json',native/'handoff_audit.json'):retain(p)
    texts=(out/'final_tests.log').read_text(encoding='utf-8-sig');assert '33 passed' in texts and 'failed' not in texts;retain(out/'final_tests.log')
    sources=out/'source_snapshot';sources.mkdir(exist_ok=False)
    for name in ('scripts/run_official_calibration_portfolio.py','scripts/gw5a_sdpb_array_model.py','scripts/ownpr_io_contract.py','scripts/ownpr_global_relayout.py','scripts/close_official_baseline_trial.py','experiments/build_official_memory_proof.py','experiments/validate_sdpb_array_model.py'):
        p=ROOT/name;target=sources/(p.name+'.txt');shutil.copyfile(p,target);retain(target)
    elapsed=time.time()-budget['start_epoch'];within=time.time()<budget['deadline_epoch']
    result=dict(status='completed_experiments_baselines_and_goal_incomplete' if within else 'failed_original_deadline',goal_complete=False,
        previous_goal_turn='progress',current_goal_turn='progress',full_fair_baselines_frozen=False,optimization_candidate_accepted=False,
        official_cases=rows,timing_eligible_candidates_not_denominators=selected,source_memory_equivalence_proved=False,
        complete_named_io_contracts_passed=True,vex_unrealized_or_pruned_pad_contract=['iBusWishbone_ERR','dBusWishbone_ERR'],
        sdpb_differential_configurations=24,sdpb_negative_mutations_rejected=6,tests_passed=33,
        rejected_tmds_candidate=c['metrics'],retained_own_tmds_fmax_mhz=112.368,retained_own_vexriscv_fmax_mhz=90.380,
        attempt_elapsed_seconds=elapsed,original_budget_seconds=budget['deadline_epoch']-budget['start_epoch'],within_original_deadline=within,within_user_one_hour=elapsed<=3600,
        source_sha256=artifacts,workspace=reserve(ROOT,0),remaining=['Unbounded memory/DSP/state equivalence','Full native I/O/pulse/endpoint/physical/configuration acceptance','Frozen competitive official denominators','All four >=20% own gains'])
    (out/'completion_audit.json').write_text(json.dumps(result,indent=2));print(json.dumps(dict(status=result['status'],seconds=elapsed,selected=selected,workspace=result['workspace']),indent=2))
    if not within:raise TimeoutError('Original trial deadline retained')


if __name__=='__main__':main()
