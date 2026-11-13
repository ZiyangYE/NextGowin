"""Record newly discharged baseline gates without treating missing gates as passed."""
import json,sys,time,shutil
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from ownpr_image_storage import load_json,sha
from workspace_budget import reserve

def main():
    r=ROOT/'results/official_encoded_contract_trial_013';reserve(ROOT,2000000);budget=load_json(r/'budget.json')
    io={x['design']:x for x in load_json(r/'encoded_io_and_frames.json')};lut=load_json(r/'native_lut_payload.json');rows=[];files=[]
    catalog=load_json(ROOT/'results/official_joint_baseline_scout_001/target_probe_catalog.json')
    for entry in catalog['designs']:
        name=entry['design']
        if name not in io:
            rows.append(dict(design=name,baseline_frozen=False,remaining=['Unbounded SDPB/DSP/state and initialization correspondence','Two unused ERR pad electrical/default realization','Design-level logical/physical/configuration acceptance']));continue
        folder=r/name;cal=load_json(folder/'reload_result.json');meta=load_json(folder/'manifest.json');source=load_json(r/'source_proofs'/name/'manifest.json')
        post=r/'uart_mapped_to_pnr_slash' if name=='uart' else r/'post_pnr_proofs'/name;proof=load_json(post/'manifest.json')
        if cal['status']!='fixed_layout_sta_calibrated' or not cal['final_hold_passed'] or not meta['bitstream_requested'] or meta['returncode']!=0:raise ValueError('Calibration gate failed')
        if source['status']!='source_elaboration_to_official_mapping_proved':raise ValueError('Source gate failed')
        expected='official_mapping_to_post_pnr_logic_proved' if name=='uart' else 'source_elaboration_to_official_post_pnr_logic_proved'
        if proof['status']!=expected or source['sdk_library_sha256']!=proof['sdk_library_sha256']:raise ValueError('Post-P&R logical gate failed')
        if name=='uart' and proof['input_sha256']['gold.v']!=sha((ROOT/entry['netlist'])):raise ValueError('Transitive proof boundary differs')
        li=next(x for x in lut if Path(x['folder']).name==name)
        if li['status']!='physical_lut_words_match_encoded_payload' or io[name]['frame']['status']!='frame_integrity_verified':raise ValueError('Physical payload gate failed')
        rows.append(dict(design=name,baseline_frozen=False,competitive_ratio=None,reported_mhz=cal['final_reported_fmax_mhz'],evaluation_mhz=cal['sta_evaluation_mhz'],
            completed=['Original-pair initial-state check and source-to-mapped induction','Post-P&R logical equivalence (UART via transitive mapped boundary)','Fixed-layout same-period and target-relative four-class STA invariants','Encoded I/O/GSR matches fixed-reference attributes','Native frame CRC/tails','Captured physical LUT truth words match FSE-addressed encoded payload'],
            post_pnr_proof=str(post.relative_to(ROOT)),ports=io[name]['io']['ports'],lut_sites=li['lut_sites'],
            remaining=['Design-level logical-to-physical routed endpoint correspondence and resource/mode acceptance, beyond unchanged-state snapshots','Account for used register/mux/carry/clock configuration in the baseline acceptance ledger']))
        files.extend([folder/n for n in ('manifest.json','reload_result.json','physical_before.json','primitive_bindings.json','io_configuration_state.json','probe.vg','probe.vo','probe.fs','target_relative.tr')]);files.extend([r/'source_proofs'/name/'manifest.json',post/'manifest.json',post/'gold.v',post/'models.v'])
    files.extend([r/n for n in ('encoded_io_and_frames.json','native_lut_payload.json','lut_payload_negative_control.json')])
    result=dict(status='baseline_gate_progress_not_frozen',designs=rows,source_sha256={str(p.relative_to(ROOT)):sha(p) for p in files},wall_seconds=time.time()-budget['start_epoch'],within_original_deadline=time.time()<budget['deadline_epoch'],tests_passed=16,goal_complete=False,
        diagnostics=['UART direct source/post-P&R and default-separator mapped/post-P&R proofs remain unproved, not counterexamples. Uniform slash flatten naming plus original-pair initial checks proves the mapped/post-P&R step.','Native encoding is permitted; frame/LUT checks do not claim complete hardware equivalence or require hardware programming.'])
    if not result['within_original_deadline']:raise TimeoutError('Original trial budget expired')
    snap=r/'sources';snap.mkdir(exist_ok=True)
    for file in ['experiments/native_fixed_layout_sta.py','scripts/audit_official_source_equivalence.py','scripts/audit_vendor_lut_payload.py',str(Path(__file__).relative_to(ROOT))]:shutil.copyfile(ROOT/file,snap/Path(file).name)
    result['source_snapshot_sha256']={p.name:sha(p) for p in snap.iterdir()}
    (r/'completion_audit.json').write_text(json.dumps(result,indent=2));print(json.dumps({'status':result['status'],'wall_seconds':result['wall_seconds'],'designs':[(x['design'],x.get('reported_mhz')) for x in rows]},indent=2))

if __name__=='__main__':main()
