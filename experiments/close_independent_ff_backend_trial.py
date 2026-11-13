"""Record full own-P&R mode bring-up and reject measured QoR regressions."""
import json,shutil,sys,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from ownpr_image_storage import load_json,sha
from workspace_budget import reserve
from run_ownpr_pin_closure import metric
from audit_ownpr_independent_ff import audit as independent_audit


def main():
    root=ROOT/'results/independent_ff_backend_trial_018';reserve(ROOT,5000000);budget=load_json(root/'budget.json');packetfile=ROOT/'results/joint_wide_move_map_trial_012/all_designs_reported_fmax_checkpoint.json';packet=load_json(packetfile);rows=[]
    cases={'uart':('uart_placement','uart_run_001/export/native','uart_run_001/route'),'int2float':('int2float_placement','int2float_hold_export_002/native','int2float_hold_001'),'tmds':('tmds_placement_002','tmds_run_002/export/native','tmds_run_002/route'),'vexriscv':('vexriscv_placement','vexriscv_run_001/export/native','vexriscv_run_001/route')}
    files=[packetfile]
    for entry in packet['designs']:
        name=entry['design'];placement,native,routes=[root/p for p in cases[name]];old=ROOT/entry['selected']['placement'];nm=load_json(native/'manifest.json');pm=load_json(placement/'manifest.json');check=load_json(native/'handoff_audit.json');before=load_json(ROOT/entry['selected']['sta']/'handoff_audit.json');current=metric(native)
        if current['hold_violations'] or current['hold_wns_ns']<0 or not current['other_checks_passed']:raise ValueError('Repaired cohort still fails hold/non-setup gates')
        for p,k in [(placement/'packed.json','packed_sha256'),(placement/'placement.json','placement_sha256'),(ROOT/entry['constraints']['cst'],'cst_sha256'),(ROOT/entry['constraints']['sdc'],'source_sdc_sha256')]:
            if sha(p)!=nm[k]:raise ValueError('Implementation or constraint provenance changed')
        oldp=load_json(old/'placement.json');newp=load_json(placement/'placement.json')
        def ff_sites(p):return {r['ff']:[r['row'],r['column'],r['slot']] for r in p['clusters'].values() if r.get('ff')}
        if ff_sites(oldp)!=ff_sites(newp):raise ValueError('FF locations changed')
        for key in ('fixed_io','hard_clusters','arithmetic_clusters','mux_clusters','wide_macros','ioreg_clusters'):
            if oldp.get(key)!=newp.get(key):raise ValueError('Fixed interface/hard/macro layout changed')
        if sha(routes/'clock_tree.json')!=sha(ROOT/entry['selected']['routes']/'clock_tree.json'):raise ValueError('Clock tree changed')
        def counts(a):return {k:v['paths'] for k,v in a['timing']['path_coverage'].items() if 'paths' in v}
        if counts(check)!=counts(before):raise ValueError('Timing class endpoint inventory changed')
        for proof in pm['proof']:
            c=proof['cec'];folder=Path(c['log']).parent
            if not c['equivalent'] or not c['inputs_unchanged'] or c['returncode'] or sha(folder/'before.blif')!=c['reference_sha256'] or sha(folder/'after.blif')!=c['candidate_sha256']:raise ValueError('State-boundary CEC evidence changed')
        mode=independent_audit(native,placement)
        if check['independent_ff_inputs']['records']!=mode['records']:raise ValueError('Independent input audit changed')
        oldcells=load_json(old/'packed.json')['modules']['top']['cells'];newcells=load_json(placement/'packed.json')['modules']['top']['cells']
        if len(oldcells)-len(newcells)!=1:raise ValueError('Expected exactly one identity LUT removed')
        rows.append(dict(design=name,metrics=current,previous_metrics=entry['metrics'],frequency_gain_mhz=current['reported_fmax_mhz']-entry['metrics']['reported_fmax_mhz'],lut_growth=-1,ff_sites_unchanged=True,clock_tree_unchanged=True,timing_class_inventory=counts(check),independent_inputs=len(mode['records']),placement=str(placement.relative_to(ROOT)),routes=str(routes.relative_to(ROOT)),native=str(native.relative_to(ROOT)),promoted_to_incumbent=False))
        files.extend([placement/'packed.json',placement/'placement.json',placement/'manifest.json',routes/'manifest.json',routes/'routes.json',routes/'clock_tree.json',native/'manifest.json',native/'handoff_audit.json',native/'probe.tr'])
    batch=metric(root/'tmds_timed_batch_run_001/export/native');old_tmds=next(r for r in packet['designs'] if r['design']=='tmds')['metrics']
    if batch['reported_fmax_mhz']>=old_tmds['reported_fmax_mhz']:raise ValueError('Batch rejection premise changed')
    negative=load_json(root/'native_negative_controls.json')
    if len(negative)!=3 or not all(r['rejected'] for r in negative):raise ValueError('Incomplete negative controls')
    report=ROOT/'carbon-advice-coopt-backend-20261112-037.md'
    if not report.is_file() or report.stat().st_size>20000:raise ValueError('Missing or oversized final consultation report')
    files.extend([report,root/'consultation_path_crosscheck.json',root/'native_negative_controls.json',root/'tmds_timed_batch_001/manifest.json',root/'tmds_timed_batch_run_001/export/native/handoff_audit.json'])
    sources=root/'sources';sources.mkdir(exist_ok=False)
    paths=['scripts/ownpr_independent_ff_inputs.py','scripts/ownpr_independent_ff_candidates.py','scripts/ownpr_ff_configuration.py','scripts/ownpr_route_design.py','scripts/audit_ownpr_independent_ff.py','scripts/audit_ownpr_native_export.py','scripts/audit_ownpr_extended_sta.py','scripts/ownpr_lut_graph_feedback.py','scripts/ownpr_lut_pin_eco.py','scripts/ownpr_frontier_partition.py','scripts/ownpr_regional_replication.py','scripts/ownpr_fast_ff_inputs.py','scripts/ownpr_pack_shared_ff.py','scripts/prepare_timed_independent_ff.py','experiments/native_import_ownpr.py','tests/test_independent_ff_backend.py','docs/INDEPENDENT_FF_BACKEND_AND_HOLD_REPAIR_20261112.md',str(Path(__file__).relative_to(ROOT))]
    for path in paths:shutil.copyfile(ROOT/path,sources/Path(path).name)
    now=time.time();result=dict(status='four_design_independent_input_handoffs_verified_no_frequency_gain',goal_complete=False,incumbents_unchanged=True,fair_baselines_frozen=False,wall_seconds=now-budget['start_epoch'],within_original_deadline=now<=budget['deadline_epoch'],rows=rows,tests_passed=28,
        batch=dict(design='tmds',changes=3,metrics=batch,decision='rejected_native_frequency_and_TNS_regression',previous_mhz=old_tmds['reported_fmax_mhz']),negative_controls=negative,
        gemini=dict(attempt=37,completed=True,final_report=str(report.relative_to(ROOT)),report_sha256=sha(report),logs_inspected=False,independent_crosscheck='Endpoint classes checked against native reports. Vex63.7MHz relaxed calibration is not a denominator; detailed unverified suggestions are not results.'),
        scope='Backend and complete own-P&R experiments under each source packet unchanged timing constraints. No frozen competitive ratio or whole-goal completion claim. Original RTL/hard-state/baseline gates remain pending.',
        failures=['Initial route worker could not resolve relative calibration paths after chdir; anchored to ROOT.','TMDS first direct Q-to-D self-feedback candidate lost setup endpoint coverage; rejected and excluded generically.','int2float first candidate failed hold; own branch repair validated by fresh complete STA.','Repair export reservation rejected before writing; lossless cleanup completed before retry.','TMDS full-priority/hold-filtered batch regressed and was rejected.'],
        workspace=reserve(ROOT,0),source_sha256={str(p.relative_to(ROOT)):sha(p) for p in files},source_snapshot_sha256={p.name:sha(p) for p in sources.iterdir()},
        next='Stop fixed-FF nearest-neighbor identity removal as the main frequency strategy. Pursue endpoint-class-aware legal IOREG and movable-FF/clock/data/mapping choices with actual loaded-route feedback; complete fair-baseline composition and hard-state gates.')
    (root/'completion_audit.json').write_text(json.dumps(result,indent=2));print(json.dumps({k:result[k] for k in ('status','wall_seconds','within_original_deadline','workspace')},indent=2))
    if not result['within_original_deadline']:raise SystemExit('Original trial deadline exceeded')


if __name__=='__main__':main()
