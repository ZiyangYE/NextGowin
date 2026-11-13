"""Bounded global routing/STA feedback loop with refresh after every acceptance.

Uses a reviewed own checkpoint. Failed or regressing candidates remain evidence;
only complete, fixed-contract STA improvements update the selected state.
"""
import argparse,json,subprocess,sys,time,shutil
from pathlib import Path
from ownpr_device import ROOT
from ownpr_image_storage import load_json,sha
from workspace_budget import reserve
from run_ownpr_pin_closure import stop_owned_process_tree

def improves(current,candidate):
    return bool(candidate.get('native_constraints_satisfied') and candidate['setup_violations']==0 and candidate['hold_violations']==0
        and candidate['setup_wns_ns']>current['setup_wns_ns']+.001 and candidate['hold_wns_ns']>=0)

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--report',type=Path,required=True);p.add_argument('--case',default='vexriscv');p.add_argument('--capture',type=Path)
    p.add_argument('--history-report',type=Path,help='Use loaded, path-budgeted restoration of matching historical route trees')
    p.add_argument('--candidate-routes',type=Path);p.add_argument('--candidate-export',type=Path)
    p.add_argument('--rounds',type=int,default=2);p.add_argument('--count',type=int,default=8);p.add_argument('--seconds',type=int,default=1800);p.add_argument('--out',type=Path,required=True);a=p.parse_args()
    if not 1<=a.rounds<=4 or not 1<=a.seconds<=3500 or bool(a.candidate_routes)!=bool(a.candidate_export):p.error('Invalid bounded cooptimization configuration')
    reserve(ROOT,2_000_000_000);started=time.monotonic();deadline=started+a.seconds;out=a.out.resolve();out.mkdir(exist_ok=False)
    shutil.copyfile(__file__,out/'runner_source.py.txt')
    row=next(r for r in load_json(a.report)['designs'] if r['design']==a.case)
    for name,h in row['selected_source_sha256'].items():
        if sha(ROOT/name)!=h:raise ValueError('Reviewed state changed: '+name)
    state={k:ROOT/v for k,v in row['selected'].items()};constraints={k:ROOT/v for k,v in row['constraints'].items()};metrics=dict(row['metrics']);capture=a.capture.resolve() if a.capture else None;count=a.count
    result=dict(status='running',initial=state,initial_metrics=metrics,rounds=[],vendor_pnr_executed=False,goal_complete=False,
        method='path_budgeted_historical_restore' if a.history_report else 'pin_priority_reroute',runner_sha256=sha(out/'runner_source.py.txt'))
    def save():
        result.update(selected=state,metrics=metrics,wall_seconds=time.monotonic()-started)
        (out/'manifest.json').write_text(json.dumps(result,indent=2,default=str)+'\n')
    def run(label,script,args,folder):
        reserve(ROOT,600_000_000);remaining=deadline-time.monotonic()
        if remaining<=0:raise TimeoutError('Cooptimization deadline exhausted')
        cmd=[sys.executable,str(ROOT/script),*map(str,args)]
        with (folder/(label+'.log')).open('w') as log:
            proc=subprocess.Popen(cmd,cwd=ROOT,stdout=log,stderr=subprocess.STDOUT,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            try:rc=proc.wait(timeout=remaining)
            except subprocess.TimeoutExpired:stop_owned_process_tree(proc);raise
        if rc:raise RuntimeError('Stage failed: '+label)
    save()
    try:
        for index in range(a.rounds):
            folder=out/f'round_{index:03d}';folder.mkdir();r=dict(index=index,status='running',source=state.copy(),source_metrics=metrics.copy(),count=count);result['rounds'].append(r);save()
            if index==0 and a.candidate_routes:
                routes=a.candidate_routes.resolve();export=a.candidate_export.resolve();r['existing_candidate']=True
            else:
                # A reused capture is accepted only for the exact current state;
                # accepting a prior round always clears it.
                if capture is None:
                    capture=folder/'pin_feedback'
                    run('capture','experiments/native_pin_feedback_probe.py',['--source',state['sta'],'--placement',state['placement'],'--out',capture],folder)
                routes=folder/'route';export=folder/'export'
                route_seconds=min(420,int(deadline-time.monotonic())-540)
                if route_seconds<60:raise TimeoutError('Insufficient time for routing, checks and full export')
                if a.history_report:
                    run('route','scripts/ownpr_budgeted_restore.py',['--placement',state['placement'],'--routes',state['routes'],'--sta',state['sta'],'--wire',state['wire'],
                        '--pin-feedback',capture,'--history',a.history_report.resolve(),'--count',min(count,8),'--seconds',route_seconds,'--out',routes],folder)
                    rm=load_json(routes/'manifest.json')
                    if not rm['budgeted_restoration_applied']:
                        if sha(routes/'routes.json')!=sha(state['routes']/'routes.json'):raise ValueError('Unapplied restore changed routes')
                        r.update(status='no_change',accepted=False,routes=str(routes));save();break
                else:
                    run('route','scripts/run_ownpr_pin_route.py',['--placement',state['placement'],'--routes',state['routes'],'--sta',state['sta'],'--pin-feedback',capture,'--count',count,'--seconds',route_seconds,'--out',routes],folder)
                run('export','scripts/export_ownpr.py',['--placement',state['placement'],'--routes',routes,'--clock-capture',state['clock_capture'],'--cst',constraints['cst'],'--sdc',constraints['sdc'],'--io-reference',constraints['io_reference'],'--seconds',min(420,int(deadline-time.monotonic())-1),'--out',export],folder)
            rm=load_json(routes/'manifest.json');em=load_json(export/'manifest.json');sm=load_json(export/'native/manifest.json')
            if rm['source_routes_sha256']!=sha(state['routes']/'routes.json') or rm['source_sta_sha256']!=sha(state['sta']/'probe.tr'):raise ValueError('Candidate uses stale feedback')
            if em['status']!='verified_handoff_complete' or sm['routes_sha256']!=sha(routes/'routes.json') or sm['packed_sha256']!=sha(state['placement']/'packed.json') or sm['placement_sha256']!=sha(state['placement']/'placement.json'):
                raise ValueError('Candidate lacks matching full export')
            if sm['cst_sha256']!=sha(constraints['cst']) or sm['source_sdc_sha256']!=sha(constraints['sdc']):raise ValueError('Candidate changes timing/I/O contract')
            candidate=em['metrics'];accepted=improves(metrics,candidate)
            r.update(status='accepted' if accepted else 'rejected',routes=str(routes),export=str(export),metrics=candidate,accepted=accepted,
                route_manifest_sha256=sha(routes/'manifest.json'),export_manifest_sha256=sha(export/'manifest.json'))
            if accepted:
                state=dict(state,routes=routes,wire=export/'wire',sta=export/'native');metrics=candidate;capture=None
            else:count=max(1,count//2)
            save()
        result['status']='complete';save()
    except Exception as error:
        result.update(status='timeout' if isinstance(error,(TimeoutError,subprocess.TimeoutExpired)) else 'failed',error=str(error));save();raise
    print(json.dumps(dict(status=result['status'],metrics=metrics,accepted=sum(r.get('accepted',False) for r in result['rounds']),wall_seconds=result['wall_seconds']),indent=2))

if __name__=='__main__':main()
