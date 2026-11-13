"""Bounded pin-feedback / own tree search / full-IO export transaction."""
import argparse,json,subprocess,sys,time,shutil
from datetime import datetime,timezone
from pathlib import Path
from ownpr_device import ROOT
from ownpr_image_storage import load_json,sha
from workspace_budget import reserve
from run_ownpr_pin_closure import metric,improves,stop_owned_process_tree


def main():
    p=argparse.ArgumentParser();p.add_argument('--report',type=Path,required=True);p.add_argument('--case',default='vexriscv');p.add_argument('--out',type=Path,required=True)
    p.add_argument('--batch-limit',type=int,default=1);p.add_argument('--count',type=int,default=3);p.add_argument('--seconds',type=int,default=3500);a=p.parse_args()
    if not 1<=a.batch_limit<=min(a.count,8) or not 1<=a.count<=12 or not 600<=a.seconds<=3500:p.error('Invalid complete attempt bounds')
    start=time.monotonic();deadline=start+a.seconds;reserve(ROOT,1_300_000_000)
    row=next(r for r in load_json(a.report)['designs'] if r['design']==a.case)
    for name,h in row['selected_source_sha256'].items():
        if sha(ROOT/name)!=h:raise ValueError('Source checkpoint changed: '+name)
    state={k:ROOT/v for k,v in row['selected'].items()};contract={k:ROOT/v for k,v in row['constraints'].items()}
    initial=metric(state['sta']);meta=load_json(state['sta']/'manifest.json')
    if sha(contract['cst'])!=meta['cst_sha256'] or sha(contract['sdc'])!=meta['source_sdc_sha256']:raise ValueError('Source interface contract changed')
    out=a.out.resolve();out.mkdir(exist_ok=False);shutil.copyfile(__file__,out/'runner_source.py.txt')
    result=dict(status='running',started_utc=datetime.now(timezone.utc).isoformat(),budget_seconds=a.seconds,source_report_sha256=sha(a.report),stages=[],initial_metrics=initial,accepted=False,vendor_pnr_executed=False,goal_complete=False)
    def save():
        result['wall_seconds']=time.monotonic()-start;(out/'manifest.json').write_text(json.dumps(result,indent=2))
    def run(name,script,args,limit):
        remain=deadline-time.monotonic()
        if remain<=1:raise TimeoutError('Complete attempt deadline exhausted')
        reserve(ROOT,500_000_000)
        cmd=[sys.executable,str(ROOT/script),*map(str,args)];record=dict(stage=name,command=cmd,status='running');result['stages'].append(record);save();stamp=time.monotonic()
        with (out/(name+'.log')).open('w') as log:
            proc=subprocess.Popen(cmd,cwd=ROOT,stdout=log,stderr=subprocess.STDOUT,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            try:rc=proc.wait(timeout=min(remain,limit))
            except subprocess.TimeoutExpired:stop_owned_process_tree(proc);record['status']='timeout';save();raise TimeoutError('Stage deadline: '+name)
        record.update(status='passed' if rc==0 else 'failed',returncode=rc,wall_seconds=time.monotonic()-stamp);save()
        if rc:raise RuntimeError('Stage failed: '+name)
    save()
    try:
        run('pins','experiments/native_pin_feedback_probe.py',['--source',state['sta'],'--placement',state['placement'],'--out',out/'pins'],450)
        run('trees','scripts/ownpr_critical_tree_rebuild.py',['--placement',state['placement'],'--routes',state['routes'],'--sta',state['sta'],'--pin-feedback',out/'pins','--count',a.count,'--batch-limit',a.batch_limit,'--seconds',240,'--search-mode','both','--include-single-sink','--out',out/'route'],310)
        change=load_json(out/'route/manifest.json')['critical_tree_rebuild'];result['changes']=change
        if change:
            run('export','scripts/export_ownpr.py',['--placement',state['placement'],'--routes',out/'route','--clock-capture',state['clock_capture'],'--cst',contract['cst'],'--sdc',contract['sdc'],'--io-reference',contract['io_reference'],'--seconds',600,'--out',out/'export'],610)
            observed=metric(out/'export/native');result.update(metrics=observed,accepted=improves(initial,observed))
            if result['accepted']:
                rel=lambda path:path.resolve().relative_to(ROOT).as_posix()
                selected={**state,'routes':out/'route','wire':out/'export/wire','sta':out/'export/native'}
                files=[selected['placement']/n for n in ('packed.json','placement.json')]+[selected['routes']/n for n in ('routes.json','nets.json','all_nets.json','clock_tree.json')]+[selected['sta']/n for n in ('manifest.json','probe.tr','handoff_audit.json')]+[selected['wire']/'wire_delays.json',contract['cst'],contract['sdc']]
                checkpoint=dict(designs=[dict(design=a.case,selected={k:rel(v) for k,v in selected.items()},constraints=row['constraints'],metrics=observed,selected_source_sha256={rel(f):sha(f) for f in files},qualified=observed['native_constraints_satisfied'],target_mhz=row.get('target_mhz'),hardware_verified=False)])
                (out/'optimization_checkpoint.json').write_text(json.dumps(checkpoint,indent=2))
        result['status']='accepted_timing_progress' if result['accepted'] else 'rejected'
    except Exception as error:
        result.update(status='timeout' if isinstance(error,TimeoutError) else 'failed',error=str(error));save();raise
    save();print(json.dumps(result,indent=2))


if __name__=='__main__':main()
