"""Bounded arithmetic-boundary remapping with fixed FF sites and full own P&R."""
import argparse,json,subprocess,sys,time,shutil
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from ownpr_image_storage import load_json,sha
from workspace_budget import reserve
from run_ownpr_pin_closure import stop_owned_process_tree,metric,improves
from select_ownpr_candidate import check_contract


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for n in ('source','placement','routes','sta','clock-capture','cst','sdc','io-reference','out'):p.add_argument('--'+n,type=Path,required=True)
    p.add_argument('--seconds',type=int,default=900);p.add_argument('--keep-chains',type=int,default=2);p.add_argument('--frontier',choices=('any','sum','cout'),default='any');p.add_argument('--policy',choices=('timing','all'),default='timing');a=p.parse_args()
    if not 1<=a.seconds<=3600:p.error('Complete attempt must fit one hour')
    reserve(ROOT,1000000000);start=time.monotonic();deadline=start+a.seconds;out=a.out.resolve();out.mkdir(exist_ok=False);shutil.copyfile(__file__,out/'runner_source.py.txt')
    r=dict(status='running',stages=[],budget_seconds=a.seconds,vendor_pnr_executed=False,timing_signoff=False,source_sha256=sha(a.source))
    def save():r['wall_seconds']=time.monotonic()-start;(out/'manifest.json').write_text(json.dumps(r,indent=2))
    def run(label,script,args):
        remaining=deadline-time.monotonic()
        if remaining<=0:raise TimeoutError('Complete arithmetic trial deadline')
        cmd=[sys.executable,str(ROOT/script),*map(str,args)];row=dict(stage=label,command=cmd,status='running');r['stages'].append(row);save();t=time.monotonic()
        with (out/(label+'.log')).open('w') as log:
            proc=subprocess.Popen(cmd,cwd=ROOT,stdout=log,stderr=subprocess.STDOUT,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            try:code=proc.wait(timeout=remaining)
            except subprocess.TimeoutExpired:stop_owned_process_tree(proc);raise TimeoutError(label+' exceeded complete trial deadline')
        row.update(status='passed' if code==0 else 'failed',returncode=code,wall_seconds=time.monotonic()-t);save()
        if code:raise RuntimeError(label+' failed')
    save()
    try:
        before=metric(a.sta);base=load_json(a.sta/'manifest.json');r['initial_metrics']=before
        if a.policy=='timing':run('select','scripts/select_ownpr_arithmetic.py',['--source',a.source.resolve(),'--placement',a.placement.resolve(),'--sta',a.sta.resolve(),'--keep-chains',a.keep_chains,'--frontier',a.frontier,'--out',out/'selection'])
        run('remap','scripts/remap_ownpr_logic.py',['--netlist',a.source.resolve(),'--arithmetic','selected' if a.policy=='timing' else 'all','--recipe','wide','--out',out/'remap']+(['--selection',out/'selection/selection.json'] if a.policy=='timing' else []))
        run('simplify','scripts/ownpr_carry_simplify.py',['--netlist',out/'remap/netlist.json','--materialize','--out',out/'simplify'])
        run('place','scripts/run_ownpr_carry_place.py',['--netlist',out/'simplify/netlist.json','--wide','--anchor-placement',a.placement.resolve(),'--anchor-sta',a.sta.resolve(),'--cst',a.cst.resolve(),'--sdc',a.sdc.resolve(),'--out',out/'placement'])
        run('implement','scripts/run_ownpr_checkpoint.py',['--placement',out/'placement','--clock-tree',a.routes.resolve()/'clock_tree.json','--clock-capture',a.clock_capture.resolve(),'--cst',a.cst.resolve(),'--sdc',a.sdc.resolve(),'--io-reference',a.io_reference.resolve(),'--seconds',max(1,int(deadline-time.monotonic())-1),'--out',out/'implementation'])
        result=load_json(out/'implementation/manifest.json');after=result['metrics'];candidate=result['selected']
        check_contract(base,load_json(Path(candidate['sta'])/'manifest.json'),load_json(a.routes/'clock_tree.json'),load_json(Path(candidate['routes'])/'clock_tree.json'))
        r.update(status='timing_selected_arithmetic_trial_complete',arithmetic_policy=a.policy,metrics=after,candidate=candidate,decision='accepted' if improves(before,after) else 'rejected_global_timing',preserved_chain_heads=load_json(out/'selection/selection.json')['preserved_chain_heads'] if a.policy=='timing' else []);save()
    except Exception as e:r.update(status='failed',error=repr(e));save();raise
    print(json.dumps({k:r[k] for k in ('status','metrics','decision','wall_seconds')},indent=2))


if __name__=='__main__':main()
