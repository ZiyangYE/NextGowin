"""Bounded placement or CEC-proved LUT-copy trial with own routing/full STA."""
import argparse,json,shutil,subprocess,sys,time
from pathlib import Path
from ownpr_device import ROOT
from ownpr_place import sha
from workspace_budget import reserve
from run_ownpr_pin_closure import metric,improves,stop_owned_process_tree
from select_ownpr_candidate import check_contract
from audit_ownpr_native_export import audit as export_audit
from audit_ownpr_joint_routes import audit as joint_audit


def read(p):return json.loads(Path(p).read_text())


def accept_resource_gain(before,after,lut_growth,min_gain_per_lut=.01):
    """Keep strict timing gates; avoid area growth for sub-resolution gains.

    This is a selectable primary-candidate policy, not a Pareto optimality
    claim. All evaluated candidates remain recorded for later comparison.
    """
    if not 0<=min_gain_per_lut<=10:raise ValueError('Invalid LUT gain threshold')
    if not improves(before,after):return False,'rejected_global_timing'
    if not before['native_constraints_satisfied'] and after['native_constraints_satisfied']:return True,'accepted_constraint_closure'
    required=max(.002,min_gain_per_lut*lut_growth) if lut_growth>0 and min_gain_per_lut>0 else 0.
    if after['setup_wns_ns']-before['setup_wns_ns']+1e-9<required:return False,'rejected_area_efficiency'
    return True,'accepted'


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('placement','routes','wire-timing','sta','clock-capture','cst','sdc','io-reference','out'):p.add_argument('--'+name,type=Path,required=True)
    mode=p.add_mutually_exclusive_group(required=True)
    mode.add_argument('--pair',nargs=2);mode.add_argument('--lut-slot',nargs=2,metavar=('INSTANCE','SLOT'));mode.add_argument('--chain-rank',type=int)
    mode.add_argument('--replicate-critical',action='store_true')
    mode.add_argument('--replicate-enables',action='store_true')
    mode.add_argument('--replicate-wide',action='store_true')
    mode.add_argument('--fast-ff-inputs',action='store_true')
    p.add_argument('--clock-inventory',type=Path)
    p.add_argument('--global-criticality',action='store_true');p.add_argument('--criticality-window',type=float,default=2.)
    p.add_argument('--min-gain-per-lut',type=float,default=.01,help='Minimum setup WNS improvement in ns per added LUT; 0 disables the area filter')
    p.add_argument('--seconds',type=int,default=600);a=p.parse_args()
    if not 1<=a.seconds<=3600:p.error('Complete cluster attempt must fit in 1..3600 seconds')
    start=time.monotonic();deadline=start+a.seconds;storage=reserve(ROOT,500_000_000);out=a.out.resolve();out.mkdir(exist_ok=False);shutil.copyfile(__file__,out/'runner_source.py.txt')
    state=dict(placement=str(a.placement.resolve()),routes=str(a.routes.resolve()),wire=str(a.wire_timing.resolve()),sta=str(a.sta.resolve()),clock_capture=str(a.clock_capture.resolve()))
    result=dict(status='running',initial=state,selected=dict(state),stages=[],evaluations=[],budget_seconds=a.seconds,storage_reservation=storage,
        vendor_placement_executed=False,vendor_routing_executed=False,open_chip_database_used=False,timing_signoff=False,full_bitstream_verified=False)
    def save():result['wall_seconds']=time.monotonic()-start;(out/'manifest.json').write_text(json.dumps(result,indent=2))
    def run(label,script,args,limit):
        reserve(ROOT,500_000_000);remaining=deadline-time.monotonic()
        if remaining<=0:raise TimeoutError('Cluster swap deadline exhausted')
        command=[sys.executable,str(ROOT/script),*map(str,args)];row=dict(stage=label,command=command,status='running');result['stages'].append(row);save();stamp=time.monotonic()
        with (out/(label+'.log')).open('w') as log:
            process=subprocess.Popen(command,cwd=ROOT,stdout=log,stderr=subprocess.STDOUT,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            try:code=process.wait(timeout=min(remaining,limit))
            except subprocess.TimeoutExpired:
                stop_owned_process_tree(process);row.update(status='timeout',seconds=time.monotonic()-stamp);save();raise TimeoutError('Cluster stage deadline: '+label)
        row.update(status='passed' if code==0 else 'failed',returncode=code,seconds=time.monotonic()-stamp);save()
        if code:raise RuntimeError('Cluster stage failed: '+label)
    save()
    try:
        base=read(a.sta/'manifest.json');best=metric(a.sta);tree=read(a.routes/'clock_tree.json')
        if base['cst_sha256']!=sha(a.cst) or base['source_sdc_sha256']!=sha(a.sdc) or base['clock_tree_sha256']!=sha(a.routes/'clock_tree.json') or base['routes_sha256']!=sha(a.routes/'routes.json'):raise ValueError('Cluster baseline constraint or routing mismatch')
        export_audit(a.sta,a.placement);joint_audit(a.placement,a.routes,a.wire_timing);result.update(initial_metrics=best,metrics=best);save()
        placement=out/'placement';route=out/'route';export=out/'export'
        if a.replicate_critical or a.replicate_enables or a.replicate_wide or a.fast_ff_inputs:
            if a.fast_ff_inputs:run('fast_ff_inputs','scripts/ownpr_fast_ff_inputs.py',['--base',a.placement.resolve(),'--sta',a.sta.resolve(),'--out',placement],180)
            else:run('replicate','scripts/ownpr_lut_replication.py',['--base',a.placement.resolve(),'--routes',a.routes.resolve(),'--sta',a.sta.resolve(),'--cst',a.cst.resolve(),'--sdc',a.sdc.resolve(),'--out',placement,'--driver-kind','wide' if a.replicate_wide else 'ordinary','--sink-kind','enable' if a.replicate_enables else 'any'],180)
            proof=read(placement/'manifest.json')['proof']
            if not proof or not all(r['cec']['equivalent'] and r['cec']['inputs_unchanged'] for r in proof):raise ValueError('Critical LUT copy lacks functional equivalence')
            result['logic_equivalence_proof']=proof
        elif a.chain_rank is not None:
            run('chain','scripts/ownpr_critical_chain_move.py',['--base',a.placement.resolve(),'--sta',a.sta.resolve(),'--rank',a.chain_rank,'--out',placement]+(['--global-criticality','--criticality-window',a.criticality_window] if a.global_criticality else []),120)
        elif a.lut_slot:
            run('slot','scripts/ownpr_lut_slot.py',['--base',a.placement.resolve(),'--lut-slot',*a.lut_slot,'--out',placement],60)
        else:
            run('swap','scripts/ownpr_cluster_swap.py',['--base',a.placement.resolve(),'--pair',*a.pair,'--out',placement],60)
        if (not (a.replicate_critical or a.replicate_enables or a.replicate_wide or a.fast_ff_inputs) and sha(placement/'packed.json')!=base['packed_sha256']) or read(placement/'placement.json')['fixed_io']!=read(a.placement/'placement.json')['fixed_io']:raise ValueError('Cluster trial changed an unapproved logical/interface contract')
        hard=bool(read(placement/'placement.json').get('hard_clusters'))
        if hard:run('hard_bindings','experiments/native_hard_placement_probe.py',['--placement',placement,'--out',out/'hard_bindings'],120)
        budget=min(300,int(deadline-time.monotonic())-100)
        if budget<1:raise TimeoutError('Insufficient routing and export budget')
        policy=read(a.routes/'manifest.json');route_args=['--search-bias',policy.get('search_bias',1),'--root-arrival-weight',policy.get('root_arrival_weight',1),'--guide-edge-delay',policy.get('guide_edge_delay_ns_surrogate',1)]
        if policy.get('guided_corridor'):route_args+=['--guided-corridor']
        if hard:route_args+=['--hard-bindings',out/'hard_bindings']
        if a.clock_inventory:route_args+=['--clock-inventory',a.clock_inventory.resolve()]
        run('route','scripts/run_ownpr_timing_route.py',['--placement',placement,'--clock-tree',a.routes.resolve()/'clock_tree.json','--reuse-routes',a.routes.resolve(),'--reuse-branches','--timeout',budget,'--out',route]+route_args,budget+30)
        remaining=int(deadline-time.monotonic())
        if remaining<1:raise TimeoutError('No time for final STA')
        run('export','scripts/export_ownpr.py',['--placement',placement,'--routes',route,'--clock-capture',a.clock_capture.resolve(),
            '--cst',a.cst.resolve(),'--sdc',a.sdc.resolve(),'--io-reference',a.io_reference.resolve(),'--seconds',remaining,'--out',export],remaining)
        sta=export/'native';wire=export/'wire';check_contract(base,read(sta/'manifest.json'),tree,read(sta/'clock_tree.json'))
        export_audit(sta,placement);joint_audit(placement,route,wire);observed=metric(sta)
        lut_count=lambda f:sum(c['type'] in ('LUT1','LUT2','LUT3','LUT4') for c in read(Path(f)/'packed.json')['modules']['top']['cells'].values())
        growth=lut_count(placement)-lut_count(a.placement);accept,decision=accept_resource_gain(best,observed,growth,a.min_gain_per_lut)
        candidate=dict(placement=str(placement),routes=str(route),wire=str(wire),sta=str(sta),clock_capture=str(a.clock_capture.resolve()))
        if hard:candidate['hard_bindings']=str(out/'hard_bindings')
        result['evaluations'].append(dict(**candidate,metrics=observed,decision=decision,lut_growth=growth,min_gain_per_lut_ns=a.min_gain_per_lut))
        if accept:result.update(selected=candidate,metrics=observed)
        result.update(status='bounded_cluster_attempt_complete',logical_netlist_byte_identical=sha(placement/'packed.json')==base['packed_sha256'],clock_tree_byte_identical=sha(route/'clock_tree.json')==base['clock_tree_sha256']);save()
    except Exception as error:
        result.update(status='timeout' if isinstance(error,TimeoutError) else 'failed',error=str(error));save();raise
    print(json.dumps(dict(status=result['status'],wall_seconds=result['wall_seconds'],metrics=result['metrics'],decision=result['evaluations'][0]['decision']),indent=2))


if __name__=='__main__':main()
