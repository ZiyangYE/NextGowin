"""Close trial017 with explicit distinction between calibration and routed QoR."""
import json,re,shutil,sys,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from ownpr_image_storage import load_json,read_text,sha
from workspace_budget import reserve


def main():
    root=ROOT/'results/independent_ff_input_trial_017';reserve(ROOT,5000000);budget=load_json(root/'budget.json')
    discovery=load_json(root/'discovery_audit.json');encoded=[load_json(root/name) for name in ('encoding_audit_001.json','encoding_audit_002.json')]
    records=[r for a in encoded for r in a['records']]
    if {(r['tile_type'],r['slot']) for r in records}!={(t,s) for t in (17,228) for s in range(8)} or len(records)!=16:raise ValueError('Incomplete encoded slot coverage')
    if any(a['status']!='sample_independent_input_modes_and_fuses_verified' for a in encoded):raise ValueError('Independent encoding audit failed')
    for a in [discovery,*encoded]:
        for p,h in a['source_sha256'].items():
            if sha(Path(p))!=h:raise ValueError('Native calibration source changed')
    source=root/'int2float_full_cfu';old=ROOT/'results/official_encoded_contract_trial_013/int2float'
    from ownpr_device import Device
    from ownpr_lut_configuration import NativeFrameProjection
    from audit_vendor_lut_payload import audit as lut_audit
    from ownpr_frame_integrity import audit as frame_audit
    from audit_native_io_configuration import check_state
    from ownpr_io_reference import load as io_reference
    projection=NativeFrameProjection(Device())
    same_payload=bool((projection.read(source/'probe.fs')==projection.read(old/'probe.fs')).all())
    def logic(path):return re.sub(r'/\*.*?\*/|//[^\n]*','',read_text(path),flags=re.S).split()
    same_logic=logic(source/'probe.vo')==logic(old/'probe.vo')
    if not(same_payload and same_logic):raise ValueError('Official payload or post-P&R logic changed')
    old_gsr=load_json(root/'int2float_full_gsr.json');ref=next(Path(k).parent for k in old_gsr['source_sha256'] if k.endswith('manifest.json'))
    io=check_state(load_json(source/'io_configuration_state.json'),io_reference(ref,source/'project.cst')['state'])
    lut=lut_audit(source);frame=frame_audit(source/'probe.fs')
    if lut['status']!='physical_lut_words_match_encoded_payload' or frame['status']!='frame_integrity_verified':raise ValueError('Native payload check failed')
    proof_records=[]
    for sub,expected in [('source_proofs','source_elaboration_to_official_mapping_proved'),('post_pnr_proofs','source_elaboration_to_official_post_pnr_logic_proved')]:
        p=old.parent/sub/'int2float';m=load_json(p/'manifest.json');script=read_text(p/'proof.ys');log=read_text(p/'proof.log')
        if m['status']!=expected or m['invocation']['returncode']!=0 or sha(p/'proof.ys')!=m['invocation']['script_sha256']:raise ValueError('Original logical proof changed')
        for file,h in m['input_sha256'].items():
            if sha(p/file)!=h:raise ValueError('Original proof input changed')
        if ('design -save original_pair' not in script or 'sat -seq 4 -set-init-undef -set-def-inputs -prove-asserts -verify' not in script or 'equiv_induct -seq 4' not in script or 'Equivalence successfully proven!' not in log):raise ValueError('Missing original initial/inductive proof obligations')
        proof_records.append(dict(folder=str(p.relative_to(ROOT)),status=m['status'],manifest_sha256=sha(p/'manifest.json'),script_sha256=sha(p/'proof.ys'),log_sha256=sha(p/'proof.log')))
    core=load_json(root/'int2float_full_core_modes_002.json');endpoints=load_json(root/'int2float_full_endpoints.json');parents=load_json(root/'int2float_full_parent_fuses.json');sta=load_json(source/'reload_result.json')
    if core['unresolved'] or core['uncovered_lut_mode_groups'] or endpoints['unresolved'] or parents['configuration_mismatches'] or parents['canonical_resource_conflicts']:raise ValueError('Physical gate is incomplete')
    if sta['status']!='fixed_layout_sta_calibrated' or not sta['final_hold_passed']:raise ValueError('STA contract failed')
    physical=dict(status='captured_logic_modes_endpoints_and_payload_matched',baseline_frozen=False,io=io,luts=lut,frames=frame,
        identical_to_proved_013_logic=same_logic,identical_to_013_payload=same_payload,source_proofs=proof_records,
        reported_fmax_mhz=sta['final_reported_fmax_mhz'],sta_evaluation_mhz=sta['sta_evaluation_mhz'],actual_target_relative=sta['phases'][-1],
        remaining='Complete the baseline-specific composition of source-clock and fixed-I/O configuration acceptance. Existing generic audits retain explicit source-mode/full-clock and all-I/O-mode scope limits; do not silently promote them to full acceptance.',
        source_sha256={str(p.relative_to(ROOT)):sha(p) for p in (source/'probe.vo',old/'probe.vo',source/'probe.fs',old/'probe.fs',source/'primitive_bindings.json',source/'reload_result.json',root/'int2float_full_core_modes_002.json',root/'int2float_full_endpoints.json',root/'int2float_full_parent_fuses.json',root/'int2float_full_cls.json')})
    (root/'int2float_baseline_readiness.json').write_text(json.dumps(physical,indent=2))
    cohort=load_json(root/'cohort_candidates.json');negative=load_json(root/'negative_controls.json');admission=load_json(root/'production_admission_negative.json')
    if len(cohort['designs'])!=4 or any(r['status']!='equivalent_unrouted_candidate' for r in cohort['designs']) or not all(r['rejected'] for r in negative) or len(admission)!=4:raise ValueError('Candidate/negative checks incomplete')
    snapshots=root/'sources';snapshots.mkdir(exist_ok=False)
    sources=['experiments/native_wide_ff_routes.py','experiments/native_independent_ff_encoding.py','experiments/native_primitive_binding_oracle.py','scripts/ownpr_independent_ff_inputs.py','scripts/ownpr_independent_ff_candidates.py','scripts/audit_independent_ff_input.py','scripts/audit_native_core_modes.py','tests/test_independent_ff_inputs.py','docs/INDEPENDENT_FF_INPUT_AND_JOINT_PACKING_20261112.md',str(Path(__file__).relative_to(ROOT))]
    for path in sources:shutil.copyfile(ROOT/path,snapshots/Path(path).name)
    now=time.time();result=dict(status='independent_ff_modes_and_joint_candidates_verified_not_routed',goal_complete=False,baseline_frozen=False,own_fmax_unchanged=True,
        wall_seconds=now-budget['start_epoch'],within_original_deadline=now<=budget['deadline_epoch'],binding_cases=80,selector_conflicts_rejected=discovery['rejected_selector_conflicts'],encoded_cases=16,
        checked_mode_bit_observations=sum(r['mode_bits'] for r in records),checked_pip_mask_bit_observations=sum(r['pip_mask_bits'] for r in records),
        core_registers=102,logical_lut_functions=293,tests_passed=26,cohort=[{k:r[k] for k in ('design','eligible_buffers','candidate_pairs','status')} for r in cohort['designs']],
        negative_controls=negative,production_admission=admission,gemini=dict(attempt=36,exit_code=1,report_exists=False,advice_accepted=False,logs_inspected=False),
        failed_unfinalized_encoding=load_json(root/'encoded/manifest.json'),workspace=reserve(ROOT,0),
        source_sha256={str(p.relative_to(ROOT)):sha(p) for p in snapshots.iterdir()},
        next='Implement selected SEL endpoint and mode handling across route extraction/import/timing feedback; verify complete native STA before a routed ranking experiment. Keep FF/clock sites fixed. Finish baseline-specific composition without treating generic audit flags as either automatic waivers or automatic proof of failure.')
    (root/'completion_audit.json').write_text(json.dumps(result,indent=2))
    print(json.dumps({k:result[k] for k in ('status','wall_seconds','within_original_deadline','workspace','cohort')},indent=2))
    if not result['within_original_deadline']:raise SystemExit('Original trial deadline exceeded')


if __name__=='__main__':main()
