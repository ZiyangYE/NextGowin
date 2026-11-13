"""Complete bounded setup-tree / hold-buffer cooptimization experiment.

An intermediate hold-infeasible tree never replaces a qualified checkpoint.
Original min/max I/O and clock constraints are preserved throughout.
"""
import argparse,json,subprocess,sys,time,shutil
from pathlib import Path
from ownpr_device import ROOT
from ownpr_image_storage import load_json,sha
from workspace_budget import reserve
from run_ownpr_pin_closure import stop_owned_process_tree

def main():
    p=argparse.ArgumentParser(description=__doc__)
    for n in ('placement','routes','sta','pin-feedback','clock-capture','cst','sdc','io-reference','out'):p.add_argument('--'+n,type=Path,required=True)
    p.add_argument('--seconds',type=int,default=1500);a=p.parse_args()
    if not 1<=a.seconds<=3500:p.error('Complete experiment must fit within one hour')
    for n in ('placement','routes','sta','pin_feedback','clock_capture','cst','sdc','io_reference','out'):setattr(a,n,getattr(a,n).resolve())
    reserve(ROOT,3_000_000_000);a.out.mkdir(exist_ok=False);start=time.monotonic();deadline=start+a.seconds
    result=dict(status='running',budget_seconds=a.seconds,stages=[],selected=None,qualified=False,vendor_pnr_executed=False,goal_complete=False)
    sources=a.out/'sources';sources.mkdir()
    for name in ('run_ownpr_joint_minmax.py','ownpr_critical_tree_rebuild.py','ownpr_hold_buffers.py','ownpr_route_eco.py','run_ownpr_hold_closure.py'):
        shutil.copyfile(ROOT/'scripts'/name,sources/name)
    result['source_sha256']={f.name:sha(f) for f in sources.iterdir()}
    def save():result['wall_seconds']=time.monotonic()-start;(a.out/'manifest.json').write_text(json.dumps(result,indent=2,default=str)+'\n')
    def run(label,script,args):
        remaining=deadline-time.monotonic()
        if remaining<=0:raise TimeoutError('Joint min/max deadline')
        row=dict(stage=label,status='running',command=[sys.executable,str(ROOT/script),*map(str,args)]);result['stages'].append(row);save()
        with (a.out/(label+'.log')).open('w') as log:
            proc=subprocess.Popen(row['command'],cwd=ROOT,stdout=log,stderr=subprocess.STDOUT,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            try:rc=proc.wait(timeout=remaining)
            except subprocess.TimeoutExpired:stop_owned_process_tree(proc);raise
        row.update(status='passed' if rc==0 else 'failed',returncode=rc);save()
        if rc:raise RuntimeError('Joint stage failed: '+label)
    save()
    try:
        run('tree','scripts/ownpr_critical_tree_rebuild.py',['--placement',a.placement,'--routes',a.routes,'--sta',a.sta,'--pin-feedback',a.pin_feedback,'--count',1,'--seconds',240,'--search-mode','both','--include-single-sink','--explore-hold-ns',.55,'--out',a.out/'tree'])
        tree=load_json(a.out/'tree/manifest.json')
        if not tree['critical_tree_rebuild']:result['status']='no_candidate';save();return
        result['provisional_tree']=tree['critical_tree_rebuild'];save()
        run('tree_export','scripts/export_ownpr.py',['--placement',a.placement,'--routes',a.out/'tree','--clock-capture',a.clock_capture,'--cst',a.cst,'--sdc',a.sdc,'--io-reference',a.io_reference,'--seconds',360,'--out',a.out/'tree_export'])
        m=load_json(a.out/'tree_export/manifest.json');state=dict(placement=a.placement,routes=a.out/'tree',sta=a.out/'tree_export/native',wire=a.out/'tree_export/wire',clock_capture=a.clock_capture)
        result['provisional_metrics']=m['metrics'];save()
        if not m['metrics']['native_constraints_satisfied']:
            (a.out/'hold_input.json').write_text(json.dumps(dict(selected=state),indent=2,default=str)+'\n')
            run('hold','scripts/run_ownpr_hold_closure.py',['--state',a.out/'hold_input.json','--cst',a.cst,'--sdc',a.sdc,'--io-reference',a.io_reference,'--internal','--count',4,'--branch-limit',128,'--rounds',2,'--seconds',min(800,int(deadline-time.monotonic())-1),'--out',a.out/'hold'])
            h=load_json(a.out/'hold/manifest.json');result['hold_metrics']=h['metrics']
            if h['metrics']['native_constraints_satisfied']:state={k:Path(v) for k,v in h['selected'].items()};m=dict(metrics=h['metrics'])
        if m['metrics']['native_constraints_satisfied']:
            result.update(selected=state,metrics=m['metrics'],qualified=True)
        else:result.update(metrics=m['metrics'],qualified=False)
        result['status']='complete';save()
    except Exception as error:
        result.update(status='timeout' if isinstance(error,(TimeoutError,subprocess.TimeoutExpired)) else 'failed',error=str(error));save();raise
    print(json.dumps({k:result.get(k) for k in ('status','qualified','metrics','hold_metrics','wall_seconds')},indent=2))

if __name__=='__main__':main()
