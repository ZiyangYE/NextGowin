"""One bounded structural synthesis/own-P&R attempt from a checked checkpoint."""
import argparse,json,re,sys,subprocess,time,shutil,math
from datetime import datetime,timezone
from pathlib import Path
from ownpr_device import ROOT
from ownpr_image_storage import load_json,sha
from workspace_budget import reserve
from run_ownpr_pin_closure import stop_owned_process_tree


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--report',type=Path,required=True);p.add_argument('--case',default='vexriscv');p.add_argument('--out',type=Path,required=True)
    p.add_argument('--route-seed-report',type=Path)
    p.add_argument('--target-mhz',type=float,default=90);p.add_argument('--groups',type=int,default=5);p.add_argument('--depth',type=int,default=4)
    p.add_argument('--transform',choices=('regions','multilevel','measured_carry','wide_neighbors','shared_ff','buffer_move'),default='regions');p.add_argument('--timing-feedback',action='store_true')
    p.add_argument('--buffer',help='Existing standalone input identity buffer for placement-only optimization')
    p.add_argument('--buffer-fraction',type=float,default=.5,help='Source-to-load-median interpolation for buffer_move')
    p.add_argument('--shared-ff-count',type=int,default=64);p.add_argument('--shared-ff-radius',type=int,default=24)
    p.add_argument('--shared-ff-global',action='store_true')
    p.add_argument('--wide-neighbor-level',type=int,choices=(5,6),default=6);p.add_argument('--wide-neighbor-count',type=int,default=1)
    p.add_argument('--carry-count',type=int,default=8);p.add_argument('--carry-batch',type=int,default=4);p.add_argument('--carry-seconds',type=int,default=600)
    p.add_argument('--native-neighbors',action='store_true')
    p.add_argument('--allow-state-roots',action='store_true')
    p.add_argument('--protect-slack',type=float)
    p.add_argument('--target-aware-protection',action='store_true')
    p.add_argument('--share-wide-luts',action='store_true')
    p.add_argument('--input-register-copies',type=int,default=0);p.add_argument('--input-copy-min-span',type=int,default=8)
    p.add_argument('--pin-frontier',action='store_true');p.add_argument('--pin-root-limit',type=int,default=256)
    p.add_argument('--arrival-feedback',type=Path)
    p.add_argument('--reuse-pin-feedback',type=Path,help='Reuse only hash-checked queries from the exact source STA')
    p.add_argument('--cut-count',type=int,default=8);p.add_argument('--cut-window',type=float,default=4.);p.add_argument('--cut-growth',type=int,default=24);p.add_argument('--gain-target-ns',type=float,default=4.)
    p.add_argument('--cut-width',type=int,choices=(6,7),default=6);p.add_argument('--min-cut-cells',type=int,default=3)
    p.add_argument('--seconds',type=int,default=2400);p.add_argument('--coverage',action='store_true');p.add_argument('--legalize-macros',action='store_true');p.add_argument('--launch-frontier',action='store_true');p.add_argument('--cluster-regions',action='store_true');a=p.parse_args()
    if not 600<=a.seconds<=3500 or not 0<a.target_mhz<=250:p.error('Invalid attempt limits')
    if a.transform=='buffer_move' and (not a.buffer or not 0<=a.buffer_fraction<=1):p.error('Buffer movement requires a buffer name and fraction in [0,1]')
    if a.buffer and a.transform!='buffer_move':p.error('--buffer only applies to buffer_move')
    if a.arrival_feedback and (a.transform!='multilevel' or not a.pin_frontier):p.error('Competing arrivals require multilevel pin-frontier mode')
    if a.reuse_pin_feedback and not a.timing_feedback:p.error('Feedback reuse requires --timing-feedback')
    if a.protect_slack is not None and (not a.timing_feedback or a.transform not in ('regions','multilevel') or not 0<=a.protect_slack<=10):p.error('Placement protection requires structural mapping and checked pin feedback')
    if a.target_aware_protection and a.protect_slack is None:p.error('Target-aware protection requires an explicit protection margin and checked feedback')
    if a.share_wide_luts and (a.transform!='multilevel' or not a.legalize_macros):p.error('Shared wide LUTs require multilevel mapping and pattern legalization')
    if not 0<=a.input_register_copies<=8 or not 1<=a.input_copy_min_span<=100:p.error('Invalid input-copy limits')
    if a.input_register_copies and (a.transform!='multilevel' or not a.target_aware_protection):p.error('Input copies require multilevel mapping and target-period protection')
    if a.pin_frontier and (a.transform!='multilevel' or not a.timing_feedback or not 1<=a.pin_root_limit<=1024):p.error('Pin frontier requires multilevel mapping and checked feedback')
    if a.transform=='shared_ff' and (not a.timing_feedback or not 1<=a.shared_ff_count<=256 or not 1<=a.shared_ff_radius<=64):p.error('Shared FF packing requires checked timing feedback and bounded search')
    if a.shared_ff_global and (a.transform!='shared_ff' or a.route_seed_report):p.error('Global shared packing requires shared_ff and fresh data routing without a seed report')
    if a.transform=='wide_neighbors' and (not a.timing_feedback or not 1<=a.wide_neighbor_count<=4):p.error('Wide neighbors require checked pin feedback and 1..4 moves')
    if a.route_seed_report and a.transform=='measured_carry':p.error('Measured carry uses its own measured tree proposals')
    if a.transform=='measured_carry' and (not a.timing_feedback or not 60<=a.carry_seconds<=1200 or not 1<=a.carry_count<=32 or not 1<=a.carry_batch<=4):p.error('Measured carry mode needs checked feedback and bounded search')
    start=time.monotonic();reserve(ROOT,2_500_000_000);out=a.out.resolve();out.mkdir(exist_ok=False)
    shutil.copyfile(__file__,out/'runner_source.py.txt')
    result=dict(status='running',started_utc=datetime.now(timezone.utc).isoformat(),budget_seconds=a.seconds,stages=[],
        target_mhz=a.target_mhz,transform=a.transform,historical_timing_feedback=a.timing_feedback,target_aware_placement_protection=a.target_aware_protection,global_shared_placement=a.shared_ff_global,vendor_pnr_executed=False,open_chip_database_used=False,goal_complete=False,cold_rtl_run=False)
    def remain():return a.seconds-(time.monotonic()-start)
    def save():
        result['wall_seconds']=time.monotonic()-start;(out/'manifest.json').write_text(json.dumps(result,indent=2))
    def run(label,script,args):
        if remain()<=0:raise TimeoutError('Complete regional attempt deadline expired')
        if label=='candidate' and a.target_aware_protection:args=[*args,'--protect-target-mhz',a.target_mhz]
        if label=='candidate' and a.share_wide_luts:args=[*args,'--share-wide-luts']
        if label=='candidate' and a.input_register_copies:args=[*args,'--input-register-copies',a.input_register_copies,'--input-copy-min-span',a.input_copy_min_span]
        if label=='candidate' and a.pin_frontier:args=[*args,'--pin-frontier','--wire',state['wire'],'--pin-root-limit',a.pin_root_limit]
        if label=='candidate' and a.arrival_feedback:args=[*args,'--arrival-feedback',a.arrival_feedback]
        cmd=[sys.executable,str(ROOT/script),*map(str,args)];row=dict(stage=label,command=cmd,status='running');result['stages'].append(row);save();stamp=time.monotonic()
        with (out/(label+'.log')).open('w') as log:
            proc=subprocess.Popen(cmd,cwd=ROOT,stdout=log,stderr=subprocess.STDOUT,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            try:rc=proc.wait(timeout=max(.001,remain()))
            except subprocess.TimeoutExpired:stop_owned_process_tree(proc);row['status']='timeout';raise
        row.update(status='complete' if rc==0 else 'failed',wall_seconds=time.monotonic()-stamp,returncode=rc);save()
        if rc:raise RuntimeError('Failed stage: '+label)
    save()
    try:
        row=next(r for r in load_json(a.report)['designs'] if r['design']==a.case)
        for name,digest in row['selected_source_sha256'].items():
            if sha(ROOT/name)!=digest:raise ValueError('Reviewed checkpoint changed: '+name)
        state={k:ROOT/v for k,v in row['selected'].items()};contract={k:ROOT/v for k,v in row['constraints'].items()}
        native=load_json(state['sta']/'manifest.json')
        if native['cst_sha256']!=sha(contract['cst']) or native['source_sdc_sha256']!=sha(contract['sdc']):raise ValueError('Source constraints changed')
        seed_routes=state['routes']
        if a.route_seed_report:
            from ownpr_route_reference import select as select_route_reference
            seed_routes,seed_audit=select_route_reference(a.route_seed_report,a.case,state,contract)
            result['route_seed_reference']=seed_audit
            shutil.copyfile(ROOT/'scripts/ownpr_route_reference.py',out/'route_reference_source.py.txt')
        text,count=re.subn(r'(create_clock\s+-name\s+clk\s+-period\s+)[\d.]+',lambda m:m[1]+format(1000/a.target_mhz,'.14g'),contract['sdc'].read_text())
        if count!=1:raise ValueError('Single primary clock required')
        sdc=out/'target.sdc';sdc.write_text(text)
        result.update(source_report_sha256=sha(a.report),original_sdc_sha256=sha(contract['sdc']),target_sdc_sha256=sha(sdc),baseline_metrics=row['metrics'])
        candidate=out/'candidate';implementation=out/'implementation'
        feedback_args=[];feedback=a.reuse_pin_feedback.resolve() if a.reuse_pin_feedback else out/'pin_feedback'
        if a.timing_feedback:
            if a.reuse_pin_feedback:
                from ownpr_pin_feedback import load as load_feedback
                _,feedback_audit=load_feedback(feedback,state['sta'])
                result['reused_pin_feedback']=dict(path=str(feedback),audit=feedback_audit);save()
            else:run('capture','experiments/native_pin_feedback_probe.py',['--source',state['sta'],'--placement',state['placement'],'--out',feedback])
            feedback_args=['--pin-feedback',feedback,'--feedback-sta',state['sta'],'--feedback-placement',state['placement']]
        if a.transform=='regions':
            run('candidate','scripts/ownpr_path_region_coopt.py',['--base',state['placement'],'--routes',state['routes'],'--sta',state['sta'],'--target-mhz',a.target_mhz,'--groups',a.groups,'--depth',a.depth,'--out',candidate]+(['--coverage'] if a.coverage else [])+(['--legalize-macros'] if a.legalize_macros else [])+(['--launch-frontier'] if a.launch_frontier else [])+(['--cluster-regions'] if a.cluster_regions else [])+(['--allow-state-roots'] if a.allow_state_roots else [])+(['--pin-feedback',feedback,'--protect-slack',a.protect_slack] if a.protect_slack is not None else []))
            clock_replan=load_json(candidate/'plan.json')['clock_replan_required']
        elif a.transform=='multilevel':
            run('candidate','scripts/ownpr_multilevel_coopt.py',['--base',state['placement'],'--routes',state['routes'],'--sta',state['sta'],'--count',a.cut_count,'--window',a.cut_window,'--lut-growth',a.cut_growth,'--gain-target-ns',a.gain_target_ns,'--cut-width',a.cut_width,'--min-cut-cells',a.min_cut_cells,'--out',candidate]+(['--legalize-macros'] if a.legalize_macros else [])+(['--pin-feedback',feedback,'--protect-slack',a.protect_slack] if a.protect_slack is not None else []))
            clock_replan=load_json(candidate/'manifest.json')['clock_replan_required']
        elif a.transform=='buffer_move':
            run('candidate','scripts/ownpr_move_input_buffer.py',['--base',state['placement'],'--buffer',a.buffer,'--fraction',a.buffer_fraction,'--out',candidate])
            clock_replan=False
        elif a.transform=='shared_ff':
            run('candidate','scripts/ownpr_pack_shared_ff.py',['--base',state['placement'],'--routes',state['routes'],'--sta',state['sta'],'--pin-feedback',feedback,'--count',a.shared_ff_count,'--radius',a.shared_ff_radius,'--target-mhz',a.target_mhz,'--out',candidate]+(['--global-relayout'] if a.shared_ff_global else []))
            clock_replan=True
        elif a.transform=='wide_neighbors':
            run('candidate','scripts/ownpr_pack_wide_neighbors.py',['--base',state['placement'],'--routes',state['routes'],'--sta',state['sta'],'--pin-feedback',feedback,'--level',a.wide_neighbor_level,'--count',a.wide_neighbor_count,'--out',candidate])
            clock_replan=False
        else:
            run('candidate','scripts/ownpr_measured_carry_pack.py',['--placement',state['placement'],'--routes',state['routes'],'--sta',state['sta'],'--wire',state['wire'],'--pin-feedback',feedback,
                '--count',a.carry_count,'--batch-count',a.carry_batch,'--seconds',min(a.carry_seconds,int(remain())-600),'--out',candidate]+(['--native-neighbors'] if a.native_neighbors else []))
            if load_json(candidate/'manifest.json')['status']=='no_admissible_move':
                result.update(status='rejected',accepted=False,target_achieved=False,reason='No measured move satisfies the coupled budgets',implementation_executed=False);save();return
            clock_replan=False
        if remain()<600:raise TimeoutError('Insufficient remaining complete physical budget')
        clockargs=['--anchor-clock-tree',state['routes']/'clock_tree.json'] if clock_replan else ['--clock-tree',state['routes']/'clock_tree.json','--clock-capture',state['clock_capture']]
        if a.transform=='measured_carry':
            implementation.mkdir();stamp=time.monotonic()
            run('implementation','scripts/export_ownpr.py',['--placement',candidate/'placement','--routes',candidate,'--clock-capture',state['clock_capture'],
                '--cst',contract['cst'],'--sdc',sdc,'--io-reference',contract['io_reference'],'--seconds',int(remain())-300,'--out',implementation/'export'])
            exported=load_json(implementation/'export/manifest.json')
            selected=dict(placement=str(candidate/'placement'),routes=str(candidate),wire=str(implementation/'export/wire'),sta=str(implementation/'export/native'),clock_capture=str(state['clock_capture']))
            (implementation/'manifest.json').write_text(json.dumps(dict(status='checkpoint_handoff_complete',metrics=exported['metrics'],selected=selected,
                local_routing_in_candidate_stage=True,wall_seconds=time.monotonic()-stamp,vendor_pnr_executed=False),indent=2))
        else:
            reuse_args=[] if a.shared_ff_global else ['--reuse-routes',seed_routes]
            run('implementation','scripts/run_ownpr_checkpoint.py',['--placement',candidate,'--cst',contract['cst'],'--sdc',sdc,'--io-reference',contract['io_reference'],
                '--search-bias',30,'--root-arrival-weight',1,'--guide-edge-delay',.05,'--guided-corridor',
                '--seconds',int(remain())-300,'--route-seconds',min(1800 if a.shared_ff_global else 1200,int(remain())-650),'--out',implementation]+reuse_args+clockargs+feedback_args)
        physical=load_json(implementation/'manifest.json');metrics=physical['metrics'];result.update(metrics=metrics,target_achieved=metrics['native_constraints_satisfied'],accepted=False)
        selected=physical['selected'];rel=lambda x:Path(x).resolve().relative_to(ROOT).as_posix()
        pd,rd=Path(selected['placement']),Path(selected['routes'])
        files=[pd/'packed.json',pd/'placement.json',*[rd/n for n in ('routes.json','nets.json','all_nets.json','clock_tree.json')],*[implementation/'export/native'/n for n in ('manifest.json','probe.tr','handoff_audit.json')],implementation/'export/wire/wire_delays.json']
        review=dict(designs=[dict(design=a.case,selected={k:rel(v) for k,v in selected.items()},metrics=metrics,
            constraints=dict(cst=rel(contract['cst']),sdc=rel(sdc),io_reference=rel(contract['io_reference'])),selected_source_sha256={rel(f):sha(f) for f in files})])
        (out/'replay_checkpoint.json').write_text(json.dumps(review,indent=2))
        if metrics['hold_violations']==0 and metrics['other_checks_passed'] and metrics['reported_fmax_mhz']>row['metrics']['reported_fmax_mhz']+.15:
            if metrics['native_constraints_satisfied']:
                result.update(accepted=True,qualified_mhz=a.target_mhz)
                shutil.copyfile(out/'replay_checkpoint.json',out/'qualified_replay_checkpoint.json')
            elif remain()>250:
                target=math.floor((metrics['reported_fmax_mhz']-.05)*10)/10
                run('qualification','scripts/replay_coopt_checkpoint.py',['--case',a.case,'--report',out/'replay_checkpoint.json','--target-mhz',target,'--seconds',int(remain())-1,'--out',out/'qualification'])
                qm=load_json(out/'qualification/manifest.json')['metrics']
                if qm['native_constraints_satisfied'] and qm['other_checks_passed'] and qm['setup_violations']==qm['hold_violations']==0:
                    result.update(accepted=True,qualified_mhz=target,qualified_metrics=qm)
                    qrow=review['designs'][0];qrow['metrics']=qm;qrow['selected']['sta']=rel(out/'qualification/export/native');qrow['constraints']['sdc']=rel(out/'qualification/clock.sdc')
                    qrow['selected_source_sha256']={k:v for k,v in qrow['selected_source_sha256'].items() if '/export/native/' not in k}
                    for n in ('manifest.json','probe.tr','handoff_audit.json'):
                        f=out/'qualification/export/native'/n;qrow['selected_source_sha256'][rel(f)]=sha(f)
                    (out/'qualified_replay_checkpoint.json').write_text(json.dumps(review,indent=2))
        result['status']='accepted' if result['accepted'] else 'rejected';save()
    except Exception as e:
        result.update(status='timeout' if isinstance(e,(TimeoutError,subprocess.TimeoutExpired)) else 'failed',error=str(e));save();raise
    print(json.dumps({k:result.get(k) for k in ('status','wall_seconds','target_achieved','qualified_mhz','metrics')},indent=2))


if __name__=='__main__':main()
