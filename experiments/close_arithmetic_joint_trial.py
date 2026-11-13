"""Close the unchanged original architecture trial budget without claiming a win."""
import json,shutil,sys,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from ownpr_image_storage import load_json,sha
from ownpr_ff_configuration import compile_initial
from workspace_budget import reserve


def main():
    r=ROOT/'results/architecture_arithmetic_joint_trial_006';budget=load_json(r/'budget.json');reserve(ROOT,6000000)
    if time.time()>budget['deadline_epoch']-20:raise TimeoutError('Original trial deadline')
    tests=(r/'targeted_tests.log').read_text()
    if '43 passed' not in tests or 'failed' in tests:raise ValueError('Targeted checks incomplete')
    scope=[]
    for name in ('arithmetic_mixed_place_007','soft_lut4_place_011'):
        try:compile_initial(load_json(r/name/'packed.json'),load_json(r/name/'placement.json'))
        except ValueError as error:
            if 'isolated CFU' not in str(error):raise
            scope.append(dict(placement=name,status='outside_newly_enforced_single_ff_calibration_scope',reason=str(error)))
        else:raise ValueError('Sibling calibration gap was not rejected')
    compile_initial(load_json(r/'native_budget_isolated_place_016/packed.json'),load_json(r/'native_budget_isolated_place_016/placement.json'))
    cases=[];sources=[r/'budget.json',r/'targeted_tests.log']
    for name in ('arithmetic_mixed_checkpoint_008','soft_lut4_checkpoint_012','native_budget_checkpoint_017'):
        p=r/name/'manifest.json';m=load_json(p)
        if m['status'] not in ('checkpoint_handoff_complete','failed','timeout'):raise ValueError('Nonterminal implementation')
        cases.append(dict(name=name,status=m['status'],metrics=m.get('metrics'),error=m.get('error'),accepted=False));sources.append(p)
        if m['status']=='checkpoint_handoff_complete' and m['metrics']['hold_violations']==0:raise ValueError('Unexpected eligible candidate needs explicit review')
    if 'RoutingTimeout' not in str(load_json(r/'native_budget_checkpoint_017/route/manifest.json').get('error')):raise ValueError('Routing failure was not audited')
    for name in ('arithmetic_mapping_003','arithmetic_lut4_mapping_004','soft_lut4_mapping_010','native_budget_mapping_014'):
        p=r/name/'manifest.json';m=load_json(p)
        if m['status']!='equivalent_remapped_checkpoint' or not m['proof'] or not all(x['cec']['equivalent'] for x in m['proof']):raise ValueError('Missing mapped functional proof')
        sources.append(p)
    reaudit=r/'source_endpoint_reaudit_018/manifest.json';rm=load_json(reaudit)
    if not rm['actual_source_endpoint_derivation_verified'] or not rm['negative_control']['wrong_logical_binding_rejected']:raise ValueError('Source endpoint negative control missing')
    sources.extend((reaudit,r/'native_budget_mapping_014/native_boundary_budgets.json',r/'setup_worst_path_analysis.json',r/'hold_worst_path_analysis.json'))
    snapshot=r/'closeout_sources';snapshot.mkdir(exist_ok=False)
    for name in ('ownpr_source_configuration.py','ownpr_incremental_routes.py','run_ownpr_timing_route.py','run_ownpr_checkpoint.py','ownpr_ff_configuration.py','ownpr_place.py','ownpr_validate.py','ownpr_mixed_carry_pack.py','ownpr_synthesis_boundary_budgets.py','remap_ownpr_logic.py','isolate_extended_ff_cfus.py'):
        shutil.copyfile(ROOT/'scripts'/name,snapshot/name)
    for name in ('reaudit_source_configuration.py','close_arithmetic_joint_trial.py'):shutil.copyfile(ROOT/'experiments'/name,snapshot/name)
    sources.extend(snapshot.iterdir());workspace=reserve(ROOT,0);elapsed=time.time()-budget['start_epoch']
    result=dict(status='bounded_architecture_trial_closed_without_gain',original_budget=budget,elapsed_seconds=elapsed,within_original_deadline=time.time()<budget['deadline_epoch'],
        implementations=cases,calibration_scope_reaudit=scope,isolated_extended_ff_candidate_checked=True,tests_passed=43,
        selected_native_fmax_mhz_unchanged=119.034,selected_checkpoint='results/tmds_cofactor_joint_trial_005/reported_fmax_checkpoint.json',
        own_vex_fmax_mhz_unchanged=90.380,goal_complete=False,fair_official_baselines_frozen=False,minimum_competitive_ratio=None,
        new_algorithm_performance_generalization_proved=False,hardware_verified=False,full_bitstream_verified=False,
        pending=['General joint arithmetic/LUT choice mapping with actual across-boundary timing and controlled physical implementation','Four-design routed evaluation and each >=1.20 fair official baseline','Official baseline hold/provenance/unused Vex ERR pad coverage and unbounded SDPB/MULTALU proof','Broader physical/configuration-mode verification'],
        workspace=workspace,source_sha256={str(f.resolve()):sha(f) for f in sources})
    if not result['within_original_deadline']:raise TimeoutError('Original trial deadline exceeded')
    (r/'completion_audit.json').write_text(json.dumps(result,indent=2));print(json.dumps({k:result[k] for k in ('status','elapsed_seconds','within_original_deadline','selected_native_fmax_mhz_unchanged')}))


if __name__=='__main__':main()
