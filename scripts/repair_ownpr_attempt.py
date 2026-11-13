"""Resume a failed own-route attempt without resetting its absolute deadline."""
import argparse,json,sys,subprocess,time,math,shutil
from pathlib import Path
from datetime import datetime,timezone
from ownpr_device import ROOT
from ownpr_image_storage import load_json,sha
from workspace_budget import reserve
from run_ownpr_pin_closure import stop_owned_process_tree


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--attempt',type=Path,required=True);p.add_argument('--base-report',type=Path,required=True);p.add_argument('--out',type=Path,required=True);a=p.parse_args()
    source=a.attempt.resolve();original=load_json(source/'manifest.json');start=datetime.fromisoformat(original['started_utc']);budget=original['budget_seconds']
    if original['status']!='failed' or budget>3600:raise ValueError('Expected a bounded failed attempt')
    if sha(a.base_report)!=original['source_report_sha256']:raise ValueError('Original feedback baseline changed')
    def remain():return budget-(datetime.now(timezone.utc)-start).total_seconds()
    if remain()<600:raise TimeoutError('Insufficient original deadline for route/export/qualification')
    reserve(ROOT,1000000000);out=a.out.resolve();out.mkdir(exist_ok=False);shutil.copyfile(__file__,out/'runner_source.py.txt')
    row=load_json(a.base_report)['designs'][0]
    for name,h in row['selected_source_sha256'].items():
        if sha(ROOT/name)!=h:raise ValueError('Original feedback artifact changed')
    state={k:ROOT/v for k,v in row['selected'].items()};contract={k:ROOT/v for k,v in row['constraints'].items()};placement=source/'candidate';implementation=source/'implementation'
    result=dict(status='running',original_attempt=str(source),original_manifest_sha256=sha(source/'manifest.json'),original_started_utc=start.isoformat(),budget_seconds=budget,stages=[],accepted=False,goal_complete=False,vendor_pnr_executed=False)
    def save():
        result['total_elapsed_upper_bound_seconds']=budget-remain();(out/'manifest.json').write_text(json.dumps(result,indent=2))
    def run(label,script,args):
        if remain()<=0:raise TimeoutError('Original complete-attempt deadline expired')
        cmd=[sys.executable,str(ROOT/script),*map(str,args)];entry=dict(stage=label,command=cmd,status='running');result['stages'].append(entry);save();stamp=time.monotonic()
        with (out/(label+'.log')).open('w') as log:
            proc=subprocess.Popen(cmd,cwd=ROOT,stdout=log,stderr=subprocess.STDOUT,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            try:rc=proc.wait(timeout=remain())
            except subprocess.TimeoutExpired:stop_owned_process_tree(proc);raise TimeoutError('Original complete-attempt deadline expired')
        entry.update(status='complete' if rc==0 else 'failed',returncode=rc,wall_seconds=time.monotonic()-stamp);save()
        if rc:raise RuntimeError('Failed resumed stage: '+label)
    save()
    try:
        run('route','scripts/run_ownpr_timing_route.py',['--placement',placement,'--clock-tree',implementation/'clock_tree.json','--hard-bindings',implementation/'hard_bindings',
            '--resume-checkpoint',source/'resume_checkpoint','--resume-negotiation','--pin-feedback',source/'pin_feedback','--feedback-sta',state['sta'],'--feedback-placement',state['placement'],
            '--search-bias',30,'--root-arrival-weight',1,'--guide-edge-delay',.05,'--guided-corridor','--timeout',max(1,min(600,int(remain())-400)),'--out',out/'route'])
        run('export','scripts/export_ownpr.py',['--placement',placement,'--routes',out/'route','--clock-capture',implementation/'clock_encoding','--cst',contract['cst'],'--sdc',source/'target.sdc',
            '--io-reference',contract['io_reference'],'--seconds',max(1,int(remain())-270),'--out',out/'export'])
        metrics=load_json(out/'export/manifest.json')['metrics'];result['metrics']=metrics;rel=lambda x:Path(x).resolve().relative_to(ROOT).as_posix()
        selected=dict(placement=rel(placement),routes=rel(out/'route'),wire=rel(out/'export/wire'),sta=rel(out/'export/native'),clock_capture=rel(implementation/'clock_encoding'))
        files=[placement/'packed.json',placement/'placement.json',*[out/'route'/n for n in ('routes.json','nets.json','all_nets.json','clock_tree.json')],*[out/'export/native'/n for n in ('manifest.json','probe.tr','handoff_audit.json')],out/'export/wire/wire_delays.json']
        review=dict(designs=[dict(design=row['design'],selected=selected,metrics=metrics,constraints=dict(cst=rel(contract['cst']),sdc=rel(source/'target.sdc'),io_reference=rel(contract['io_reference'])),selected_source_sha256={rel(f):sha(f) for f in files})])
        (out/'replay_checkpoint.json').write_text(json.dumps(review,indent=2))
        if metrics['other_checks_passed'] and metrics['hold_violations']==0 and metrics['reported_fmax_mhz']>row['metrics']['reported_fmax_mhz']+.15:
            target=original['target_mhz'] if metrics['native_constraints_satisfied'] else math.floor((metrics['reported_fmax_mhz']-.05)*10)/10
            if not metrics['native_constraints_satisfied']:
                if remain()<250:raise TimeoutError('No original budget left for frequency qualification')
                run('qualification','scripts/replay_coopt_checkpoint.py',['--case',row['design'],'--report',out/'replay_checkpoint.json','--target-mhz',target,'--seconds',int(remain())-1,'--out',out/'qualification'])
                qm=load_json(out/'qualification/manifest.json')['metrics']
                if qm['native_constraints_satisfied'] and qm['other_checks_passed'] and qm['setup_violations']==qm['hold_violations']==0:
                    r=review['designs'][0];r['metrics']=qm;r['selected']['sta']=rel(out/'qualification/export/native');r['constraints']['sdc']=rel(out/'qualification/clock.sdc')
                    r['selected_source_sha256']={k:v for k,v in r['selected_source_sha256'].items() if '/export/native/' not in k}
                    for n in ('manifest.json','probe.tr','handoff_audit.json'):
                        f=out/'qualification/export/native'/n;r['selected_source_sha256'][rel(f)]=sha(f)
                    result.update(accepted=True,qualified_mhz=target,qualified_metrics=qm)
            else:result.update(accepted=True,qualified_mhz=target,qualified_metrics=metrics)
            if result['accepted']:(out/'qualified_replay_checkpoint.json').write_text(json.dumps(review,indent=2))
        result['status']='accepted' if result['accepted'] else 'rejected';save()
    except Exception as error:
        result.update(status='timeout' if isinstance(error,TimeoutError) else 'failed',error=str(error));save();raise
    print(json.dumps({k:result.get(k) for k in ('status','total_elapsed_upper_bound_seconds','qualified_mhz','metrics')},indent=2))


if __name__=='__main__':main()
