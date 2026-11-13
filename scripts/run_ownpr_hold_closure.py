"""Bounded CEC/routing/STA hold-buffer loop with explicit infeasible exploration."""
import argparse,json,subprocess,sys,time
from pathlib import Path
from ownpr_device import ROOT
from ownpr_place import sha
from run_ownpr_pin_closure import metric,improves,stop_owned_process_tree
from select_ownpr_candidate import check_contract
from audit_ownpr_native_export import audit
from workspace_budget import reserve


def read(p):return json.loads(Path(p).read_text())


def exploration_improves(initial,before,after,old_timing,new_timing,allow_existing_pulse_failure=False):
    """An infeasible search checkpoint, never a qualified/selected result."""
    other=after['other_checks_passed']
    if not other and allow_existing_pulse_failure:
        other=(old_timing['async_constraints_passed'] and new_timing['async_constraints_passed']
            and not old_timing['minimum_pulse_width_passed']
            and new_timing['pulse_width_checks']==old_timing['pulse_width_checks']
            and new_timing['minimum_pulse_width_slack_ns']>=old_timing['minimum_pulse_width_slack_ns'])
    return bool(other and after['setup_wns_ns']>=initial['setup_wns_ns']-.03
        and (after['hold_violations'],-after['hold_wns_ns'])<(before['hold_violations'],-before['hold_wns_ns']))


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for n in ('state','cst','sdc','io-reference','out'):p.add_argument('--'+n,type=Path,required=True)
    p.add_argument('--rounds',type=int,default=4);p.add_argument('--count',type=int,default=3);p.add_argument('--seconds',type=int,default=600)
    p.add_argument('--branch-limit',type=int,default=1);p.add_argument('--clock-inventory',type=Path)
    p.add_argument('--internal',action='store_true',help='Enable checked FF-Q data-branch hold buffers')
    p.add_argument('--explore-with-existing-pulse-failures',action='store_true',help='Retain only an infeasible exploration checkpoint when existing pulse failure does not worsen; never relax qualification');a=p.parse_args()
    if not 1<=a.rounds<=8 or not 1<=a.count<=8 or not 1<=a.branch_limit<=256 or not 1<=a.seconds<=3600:p.error('Invalid bounded hold-repair limits')
    start=time.monotonic();deadline=start+a.seconds;reserve(ROOT,a.rounds*1_000_000_000);state=read(a.state)['selected'];base=read(Path(state['sta'])/'manifest.json');tree=read(Path(state['routes'])/'clock_tree.json')
    if sha(a.cst)!=base['cst_sha256'] or sha(a.sdc)!=base['source_sdc_sha256']:raise ValueError('Hold closure changed constraints')
    audit(state['sta'],state['placement']);initial=metric(state['sta']);out=a.out.resolve();out.mkdir(exist_ok=False)
    r=dict(status='running',initial=state,selected=state,exploration_state=state,initial_metrics=initial,metrics=initial,rounds=[],budget_seconds=a.seconds,vendor_pnr_executed=False,timing_signoff=False,full_bitstream_verified=False)
    def save():r['wall_seconds']=time.monotonic()-start;(out/'manifest.json').write_text(json.dumps(r,indent=2))
    def run(label,script,args):
        remaining=deadline-time.monotonic()
        if remaining<=0:raise TimeoutError('Complete hold-repair deadline')
        cmd=[sys.executable,str(ROOT/script),*map(str,args)];r['rounds'][-1]['commands'].append(cmd);save()
        with (out/(label+'.log')).open('w') as log:
            proc=subprocess.Popen(cmd,cwd=ROOT,stdout=log,stderr=subprocess.STDOUT,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            try:code=proc.wait(timeout=remaining)
            except subprocess.TimeoutExpired:stop_owned_process_tree(proc);raise
        if code:raise RuntimeError(label+' failed')
    save()
    try:
        for i in range(a.rounds):
            if metric(state['sta'])['hold_violations']==0:break
            remaining=int(deadline-time.monotonic())
            if remaining<110:r['stop_reason']='insufficient_complete_trial_budget';break
            folder=out/f'round_{i:03d}';folder.mkdir();row=dict(source=dict(state),commands=[]);r['rounds'].append(row);save()
            run(f'prepare_{i}','scripts/ownpr_hold_buffers.py',['--base',state['placement'],'--sta',state['sta'],'--count',a.count,'--branch-limit',a.branch_limit,'--out',folder/'placement']+(['--internal'] if a.internal else []))
            hard=bool(read(folder/'placement/placement.json').get('hard_clusters'))
            if hard:run(f'bindings_{i}','experiments/native_hard_placement_probe.py',['--placement',folder/'placement','--out',folder/'hard_bindings'])
            policy=read(Path(state['routes'])/'manifest.json')
            route_args=['--search-bias',policy.get('search_bias',1),'--root-arrival-weight',policy.get('root_arrival_weight',1),'--guide-edge-delay',policy.get('guide_edge_delay_ns_surrogate',1)]
            if policy.get('guided_corridor'):route_args+=['--guided-corridor']
            row['inherited_route_policy']=dict(zip(route_args[::2],route_args[1::2]));row['inherited_route_policy']['guided_corridor']=bool(policy.get('guided_corridor'))
            run(f'route_{i}','scripts/run_ownpr_timing_route.py',['--placement',folder/'placement','--clock-tree',Path(state['routes'])/'clock_tree.json','--reuse-routes',state['routes'],'--reuse-branches','--timeout',min(300,max(1,int(deadline-time.monotonic())-180)),'--out',folder/'route']+route_args+(['--hard-bindings',folder/'hard_bindings'] if hard else [])+(['--clock-inventory',a.clock_inventory.resolve()] if a.clock_inventory else []))
            run(f'export_{i}','scripts/export_ownpr.py',['--placement',folder/'placement','--routes',folder/'route','--clock-capture',state['clock_capture'],'--cst',a.cst.resolve(),'--sdc',a.sdc.resolve(),'--io-reference',a.io_reference.resolve(),'--seconds',max(1,int(deadline-time.monotonic())-1),'--out',folder/'export'])
            candidate=dict(placement=str(folder/'placement'),routes=str(folder/'route'),sta=str(folder/'export/native'),wire=str(folder/'export/wire'),clock_capture=state['clock_capture'])
            if hard:candidate['hard_bindings']=str(folder/'hard_bindings')
            check_contract(base,read(folder/'export/native/manifest.json'),tree,read(folder/'route/clock_tree.json'));after=metric(candidate['sta']);before=metric(state['sta']);row.update(candidate=candidate,metrics=after)
            if improves(initial,after):r.update(selected=candidate,metrics=after);row['decision']='accepted_hard_constraints_repaired';break
            old_timing=read(Path(state['sta'])/'handoff_audit.json')['timing'];new_timing=read(Path(candidate['sta'])/'handoff_audit.json')['timing']
            if exploration_improves(initial,before,after,old_timing,new_timing,a.explore_with_existing_pulse_failures):
                state=candidate;r['exploration_state']=state;row['decision']='infeasible_exploration_only'
            else:row['decision']='rejected';break
            save()
        r['status']='bounded_hold_repair_complete';save()
    except Exception as e:r.update(status='failed',error=repr(e));save();raise
    print(json.dumps({k:r[k] for k in ('status','metrics','wall_seconds')},indent=2))


if __name__=='__main__':main()
