"""Audit the scope-limited trial002 without promoting vendor baselines."""
import json,re,shutil,time
from pathlib import Path
from workspace_budget import reserve
from ownpr_image_storage import load_json,sha
from ownpr_inverter_view_proof import compare_views
from project_timing import parse
ROOT=Path(__file__).resolve().parents[1]


def main():
    reserve(ROOT,5_000_000);out=ROOT/'results/native_inverter_sta_trial_002';budget=load_json(out/'budget.json')
    rows=[];artifacts={}
    cases={'uart':'uart100_control_proof','int2float':'int2float_control_proof','tmds':'tmds_control_proof','vexriscv':'vexriscv_recorded_control_detached'}
    def retain(file):artifacts[str(file.relative_to(ROOT))]=sha(file)
    for design,name in cases.items():
        folder=out/name;meta=load_json(folder/'manifest.json');result=load_json(folder/'reload_result.json');norm=load_json(folder/'inverter_normalization.json')
        assert meta['status']==result['status']=='fixed_layout_sta_calibrated' and meta['returncode']==0 and meta['within_original_deadline']
        assert meta['input_copy_unchanged'] and meta['cst_copy_unchanged'] and sha(folder/'probe.vg')==meta['source_netlist_sha256']
        assert not norm['errors'] and all(norm['physical_state_checks'].values()) and not compare_views(norm['before'],norm['after'])['errors']
        assert result['normalization_semantics_verified'] and not result['observation_only'] and result['final_hold_passed']
        assert not result['full_fair_baseline_accepted']
        for phase in result['phases']:assert phase['physical_state_unchanged']
        for mode,err in result['same_period_maximum_envelope_error_ns'].items():assert err==0
        for mode,item in result['changed_period_invariants'].items():assert max(item['maximum_errors_ns'].values())<=.0011
        sdc=(folder/'target_relative.sdc').read_text();period=float(re.search(r'-period\s+([\d.]+)',sdc)[1])
        timing=parse((folder/'target_relative.tr').read_text(),sdc,dict(part='GW5AST-LV138FPG676AC1/I0',clock_periods_ns={'clk':period}))
        assert not [v for v in timing['violations'] if v.get('analysis')!='setup' and v.get('kind')!='endpoint_summary']
        row=dict(design=design,case=name,reported_fmax_mhz=result['final_reported_fmax_mhz'],evaluation_mhz=result['sta_evaluation_mhz'],
            hold_passed=True,actual_constraint_closed=timing['timing_closed'],baseline_frozen=False,
            envelope_counts={m:r['candidate_endpoints'] for m,r in result['same_period_validation'].items()},
            control_inverter_identities=len(norm['representation_proof']['control_inverter_proofs']))
        if design!='vexriscv':
            proof=out/(design+'_source_acceptance');p=load_json(proof/'manifest.json')
            assert p['status']=='source_elaboration_to_official_mapping_proved' and p['initial_base_steps']==p['induction_steps']==4
            assert p['mapped_sha256']==meta['source_netlist_sha256'] and p['implementation_sha256']==sha(proof/'runner_source.py.txt')
            log=(proof/'proof.log').read_text()
            assert log.count('SAT proof finished - no model found: SUCCESS!')>=2 and 'Equivalence successfully proven!' in log
            for file in ('manifest.json','proof.ys','proof.log','runner_source.py.txt'):retain(proof/file)
            row['source_proof']='initial_pair_base_plus_observation_base_and_induction'
        else:row['source_proof']='rejected_unsupported_SDPB'
        for file in ('manifest.json','reload_result.json','inverter_normalization.json','runner_source.py.txt','initial_clock.sdc','target_relative.sdc','target_relative.tr'):retain(folder/file)
        rows.append(row)
    tests=(out/'targeted_tests.log').read_text(encoding='utf-8-sig');assert '29 passed' in tests and 'failed' not in tests
    retain(out/'targeted_tests.log')
    snapshot=out/'source_snapshot';snapshot.mkdir(exist_ok=False)
    for file in ('scripts/native_inverter_normalization.py','scripts/ownpr_inverter_view_proof.py','scripts/ownpr_sta_invariants.py','scripts/audit_official_source_equivalence.py','scripts/close_native_inverter_trial.py','experiments/native_fixed_layout_sta.py'):
        path=ROOT/file;shutil.copyfile(path,snapshot/(path.name+'.txt'));retain(snapshot/(path.name+'.txt'))
    superseded=[f.parent.name for f in out.glob('*/manifest.json') if '_source_' in f.parent.name and not f.parent.name.endswith('_source_acceptance')]
    (out/'superseded_source_proof_audit.json').write_text(json.dumps(dict(cases=superseded,final_acceptance_allowed=False,reason='Only *_source_acceptance packets contain the final independently tested original-pair initial-state and interface/black-box gates.'),indent=2))
    elapsed=time.time()-budget['start_epoch'];within=time.time()<budget['deadline_epoch']
    result=dict(status='calibration_completed_fair_baselines_and_goal_incomplete' if within else 'failed_original_deadline',goal_complete=False,
        previous_goal_turn='no_progress_goal_wording_only',current_goal_turn='progress',four_design_calibration_complete=True,
        full_fair_baselines_frozen=False,new_own_frequency_gain=False,retained_own_vexriscv_reported_fmax_mhz=90.380,
        cases=rows,tests_passed=29,attempt_elapsed_seconds=elapsed,original_budget_seconds=budget['deadline_epoch']-budget['start_epoch'],
        within_original_deadline=within,within_user_one_hour=elapsed<=3600,source_sha256=artifacts,workspace=reserve(ROOT,0),
        remaining=['Final common-engine cohort replay','Vex SDPB source equivalence','Independent complete I/O/endpoint/pulse/physical/configuration audit','Competitive common vendor portfolio and frozen references','All four own-flow >=20% gains with complete checks'])
    (out/'completion_audit.json').write_text(json.dumps(result,indent=2))
    print(json.dumps(dict(status=result['status'],elapsed=elapsed,workspace=result['workspace'],cases=rows),indent=2))
    if not within:raise TimeoutError('Original deadline retained; no promotion')


if __name__=='__main__':main()
