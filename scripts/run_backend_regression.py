"""Replay frozen non-primary designs after common backend changes.

This is compatibility regression of fixed implementations, not evidence of
fresh synthesis/P&R optimization quality or generalization of a new heuristic.
"""
import argparse,json,subprocess,sys,time
from pathlib import Path
from ownpr_device import ROOT
from workspace_budget import reserve
from run_ownpr_pin_closure import metric,stop_owned_process_tree
from ownpr_image_storage import compact,sha


def passed(m):
    return m['native_constraints_satisfied'] and m['other_checks_passed'] and m['setup_violations']==m['hold_violations']==0


def main():
    p=argparse.ArgumentParser();p.add_argument('--policy',type=Path,required=True);p.add_argument('--out',type=Path,required=True);a=p.parse_args()
    start=time.monotonic();deadline=start+3500;reserve(ROOT,1_200_000_000)
    policy=json.loads(a.policy.read_text());out=a.out.resolve();out.mkdir(exist_ok=False)
    result=dict(status='running',policy_sha256=sha(a.policy),budget_seconds=3500,scope='Frozen implementation backend replay; not fresh RTL synthesis or heuristic generalization',cases=[],vendor_pnr_executed=False,goal_complete=False)
    def save():
        result['wall_seconds']=time.monotonic()-start;(out/'manifest.json').write_text(json.dumps(result,indent=2))
    save()
    for name,baseline in policy['regression']['baselines'].items():
        entry=dict(design=name,baseline_mhz=baseline['qualified_mhz'],guard_mhz=baseline['guard_mhz'],trials=[]);result['cases'].append(entry)
        report=out/(name+'_checkpoint.json');report.write_text(json.dumps(dict(designs=[baseline['checkpoint']]),indent=2))
        for label,target in [('baseline',entry['baseline_mhz']),('guard',entry['guard_mhz'])]:
            remaining=deadline-time.monotonic()
            if remaining<30:entry['status']='deadline_failure';save();raise TimeoutError('Regression deadline')
            reserve(ROOT,1_000_000_000);folder=out/(name+'_'+label);seconds=min(600,int(remaining)-1)
            cmd=[sys.executable,str(ROOT/'scripts/replay_coopt_checkpoint.py'),'--case',name,'--report',str(report),'--target-mhz',str(target),'--seconds',str(seconds),'--out',str(folder)]
            row=dict(target_mhz=target,command=cmd,status='running');entry['trials'].append(row);save()
            with (out/(name+'_'+label+'.log')).open('w') as log:
                proc=subprocess.Popen(cmd,cwd=ROOT,stdout=log,stderr=subprocess.STDOUT,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
                try:rc=proc.wait(timeout=min(seconds+10,deadline-time.monotonic()))
                except subprocess.TimeoutExpired:stop_owned_process_tree(proc);row['status']='timeout';entry['status']='execution_failure';save();break
            row.update(status='completed' if rc==0 else 'execution_failed',returncode=rc)
            if rc:entry['status']='execution_failure';save();break
            m=metric(folder/'export/native');row['metrics']=m;row['passed']=passed(m)
            image=folder/'export/native/probe.fs'
            if image.exists():reserve(ROOT,image.stat().st_size+1_000_000);row['image_compaction']=compact(image)
            if passed(m):entry['status']='baseline_pass' if label=='baseline' else 'within_5_percent_guard';save();break
            if m['hold_violations'] or not m['other_checks_passed']:entry['status']='timing_regression_requires_diagnosis';save();break
            if label=='guard':entry['status']='significant_setup_regression'
            save()
    result['status']='backend_regression_passed' if all(r['status']=='baseline_pass' for r in result['cases']) else 'review_required'
    save();print(json.dumps({k:v for k,v in result.items() if k!='cases'},indent=2))
    print(json.dumps([{k:v for k,v in c.items() if k!='trials'} for c in result['cases']],indent=2))


if __name__=='__main__':main()
