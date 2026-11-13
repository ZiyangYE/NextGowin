"""Record the bounded trial020 evidence; never certify the joint objective."""
import json,sys,time,shutil
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from ownpr_image_storage import load_json,sha
from workspace_budget import reserve


def main():
    out=ROOT/'results/clock_cone_joint_trial_020';budget=load_json(out/'budget.json')
    if time.time()>budget['deadline_epoch']:raise TimeoutError('Original trial020 deadline expired')
    reserve(ROOT,20000000)
    packet_path=ROOT/'results/joint_wide_move_map_trial_012/all_designs_reported_fmax_checkpoint.json'
    packet=load_json(packet_path);best={r['design']:r['metrics']['reported_fmax_mhz'] for r in packet['designs']}
    for row in packet['designs']:
        for name,digest in row['selected_source_sha256'].items():
            if sha(ROOT/name)!=digest:raise ValueError('Incumbent dependency changed: '+name)
    candidates=[
        ('int2float','cone','int2float_cone_run_001/export'),
        ('int2float','latency_clock','int2float_clock_only_001/export'),
        ('int2float','global_layout','int2float_global_run_001/export'),
        ('int2float','slack_clock','int2float_clock_slack_001/export'),
        ('int2float','geometry_minmax','int2float_minmax_run_001/export'),
        ('int2float','external_hold_clock','int2float_clock_external_export_002'),
        ('uart','latency_clock','uart_clock_latency_001/export'),
        ('vexriscv','geometry_default_policy','vexriscv_minmax_run_001/export'),
        ('vexriscv','geometry_restored_policy','vexriscv_policy_restored_002/export'),
        ('vexriscv','geometry_changed_trees','vexriscv_whole_changed_trees_003/export')]
    rows=[]
    for design,label,folder in candidates:
        f=out/folder/'manifest.json'
        if not f.exists():
            runner=out/folder/'..'/'manifest.json'
            if runner.exists() and load_json(runner)['status'] in ('failed','timeout'):
                m=load_json(runner)
                rows.append(dict(design=design,experiment=label,status=m['status'],decision='reject_incomplete_attempt',error=m.get('error'),manifest=str(runner.resolve().relative_to(ROOT))))
                continue
            raise ValueError('Missing final trial evidence: '+folder)
        m=load_json(f)
        if m['status']!='verified_handoff_complete':
            rows.append(dict(design=design,experiment=label,status=m['status'],decision='reject_incomplete_export',manifest=str(f.relative_to(ROOT))))
            continue
        native=out/folder/'native';n=load_json(native/'manifest.json');a=load_json(native/'handoff_audit.json');t=a['timing']
        if n['vendor_placement_executed'] or n['vendor_routing_executed']:raise ValueError('Vendor P&R in own candidate')
        for name,h in m['output_sha256'].items():
            if sha(native/name)!=h:raise ValueError('Export artifact changed')
        metrics=m['metrics']
        if metrics['reported_fmax_mhz']>best[design] and metrics['hold_violations']==0 and metrics['other_checks_passed']:
            raise ValueError('Candidate exceeds incumbent; perform full promotion audit before closeout')
        rows.append(dict(design=design,experiment=label,metrics=metrics,incumbent_mhz=best[design],decision='reject',
            four_class_paths={k:v['paths'] for k,v in t['path_coverage'].items()},pulse_width_checks=t['pulse_width_checks'],
            manifest=str(f.relative_to(ROOT)),manifest_sha256=sha(f),handoff_sha256=sha(native/'handoff_audit.json')))
    tests=(out/'final_tests.log').read_text()
    if '38 passed' not in tests or 'failed' in tests:raise ValueError('Focused tests not passed')
    tmds=load_json(out/'tmds_minmax_002/manifest.json')['search']
    if tmds['moves'] or tmds['baseline_score']!=tmds['selected_score']:raise ValueError('TMDS needs a native evaluation')
    sources=out/'sources';sources.mkdir(exist_ok=False)
    for name in ('prepare_ownpr_ioreg_cones.py','ownpr_global_relayout.py','ownpr_placement_feedback.py','ownpr_minmax_geometry.py',
                 'ownpr_arrival_evaluator.py','ownpr_clock_slack.py','run_ownpr_checkpoint.py','ownpr_joint_lut_search.py'):
        shutil.copyfile(ROOT/'scripts'/name,sources/name)
    for name in ('test_compiled_arrivals.py','test_ownpr_checkpoint_policy.py','test_ownpr_global_relayout.py','test_ownpr_clock_slack.py'):
        shutil.copyfile(ROOT/'tests'/name,sources/name)
    shutil.copyfile(__file__,sources/Path(__file__).name)
    cleanups=[]
    for suffix in ('020','020b','020c','020d'):
        f=ROOT/'cleanup'/f'superseded_html_lossless_{suffix}_20261112.json';c=load_json(f)
        if c['status'] not in ('complete','completed'):raise ValueError('Unfinished storage operation')
        cleanups.append(dict(inventory=str(f.relative_to(ROOT)),files=len(c['operations']),saved_bytes=c['saved_bytes'],sha256=sha(f)))
    result=dict(status='bounded_experiments_completed_no_incumbent_gain',goal_complete=False,incumbents_unchanged=True,
        fair_official_baselines_frozen=False,minimum_competitive_ratio=None,original_budget=budget,
        wall_seconds=time.time()-budget['start_epoch'],within_original_deadline=time.time()<budget['deadline_epoch'],
        source_packet_sha256=sha(packet_path),candidates=rows,tmds=dict(status='no_predicted_worst_slack_gain',moves=0,native_rerun=False),
        tests=dict(passed=38,log_sha256=sha(out/'final_tests.log')),
        preserved_failures=['Direct pre-route clock entry rejected missing constraint-hash fields',
            'External-launch clock export storage reservation failed before retry',
            'Generalization driver storage reservation failed before TMDS; separate bounded proposal completed',
            'Initial IOREG unit test equated native OBUF exit and IOLOGIC tile coordinates; corrected independent-coordinate assertions pass'],
        consultation=dict(report='carbon-advice-joint-clock-cones-20261112-039.md',completed=True,report_sha256=sha(ROOT/'carbon-advice-joint-clock-cones-20261112-039.md'),process_logs_read=False),
        cleanup=cleanups,workspace=reserve(ROOT,0),full_bitstream_verified=False,hardware_programmed=False,
        source_sha256={str(f.relative_to(ROOT)):sha(f) for f in sources.iterdir()})
    if not result['within_original_deadline']:raise TimeoutError('Closeout exceeded original deadline')
    (out/'completion_audit.json').write_text(json.dumps(result,indent=2))
    print(json.dumps({k:result[k] for k in ('status','wall_seconds','within_original_deadline','workspace')},indent=2))


if __name__=='__main__':main()
