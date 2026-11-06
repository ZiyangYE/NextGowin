"""Reproducible mapped-checkpoint to own clock/data P&R and full native export.

The supplied fixed-pin I/O reference is a labelled native calibration. No
vendor placement or routing runs in this implementation attempt. This entry
supports the currently calibrated LUT/FF/mode-2 carry slice, not arbitrary RTL.
"""
import argparse,json,subprocess,sys,time
from pathlib import Path
from ownpr_device import ROOT,Device,RoutingGraph
from ownpr_place import sha
from ownpr_clock_graph import ClockGraph
from ownpr_clock_configuration import ClockChoices
from ownpr_route_design import bind
from workspace_budget import reserve
from run_ownpr_pin_closure import stop_owned_process_tree


def routing_policy(inherited,search_bias=None,root_arrival_weight=None,guide_edge_delay=None,guided_corridor=False):
    result=dict(search_bias=inherited.get('search_bias',1) if search_bias is None else search_bias,
        root_arrival_weight=inherited.get('root_arrival_weight',1) if root_arrival_weight is None else root_arrival_weight,
        guide_edge_delay_ns_surrogate=inherited.get('guide_edge_delay_ns_surrogate',1) if guide_edge_delay is None else guide_edge_delay,
        guided_corridor=guided_corridor or bool(inherited.get('guided_corridor',False)))
    if not 1<=result['search_bias']<=30 or not 0<=result['root_arrival_weight']<=1 or not 0<result['guide_edge_delay_ns_surrogate']<=1:
        raise ValueError('Routing policy outside calibrated search bounds')
    return result


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for n in ('placement','cst','sdc','io-reference','out'):p.add_argument('--'+n,type=Path,required=True)
    p.add_argument('--clock-tree',type=Path);p.add_argument('--clock-capture',type=Path)
    p.add_argument('--reuse-routes',type=Path,help='Reuse matching ordinary branches; new clocks and whole-tree timing are still checked')
    p.add_argument('--anchor-clock-tree',type=Path,help='Preserve unchanged sink paths and attach changed sinks; generates a new encoding capture')
    p.add_argument('--search-bias',type=float);p.add_argument('--root-arrival-weight',type=float);p.add_argument('--guide-edge-delay',type=float)
    p.add_argument('--pin-feedback',type=Path);p.add_argument('--feedback-sta',type=Path);p.add_argument('--feedback-placement',type=Path)
    p.add_argument('--seconds',type=int,default=600);p.add_argument('--route-seconds',type=int,default=300);p.add_argument('--guided-corridor',action='store_true');a=p.parse_args()
    if not 1<=a.seconds<=3600:p.error('Complete attempt must fit 1..3600 seconds')
    if not 1<=a.route_seconds<=3000:p.error('Route stage must fit 1..3000 seconds inside the complete attempt')
    if bool(a.clock_tree)!=bool(a.clock_capture):p.error('Reused clock tree and capture must be supplied together')
    if sum(v is not None for v in (a.pin_feedback,a.feedback_sta,a.feedback_placement)) not in (0,3):p.error('Historical feedback needs capture, STA and source placement together')
    if a.anchor_clock_tree and a.clock_tree:p.error('Choose either complete clock reuse or endpoint retargeting')
    start=time.monotonic();deadline=start+a.seconds;storage=reserve(ROOT,800_000_000);out=a.out.resolve();out.mkdir(exist_ok=False)
    import shutil
    shutil.copyfile(__file__,out/'runner_source.py.txt')
    placement=a.placement.resolve();result=dict(status='running',budget_seconds=a.seconds,stages=[],storage_reservation=storage,
        vendor_placement_executed=False,vendor_routing_executed=False,open_chip_database_used=False,timing_signoff=False,full_bitstream_verified=False)
    result['source_sha256']={str(f):sha(f) for f in (placement/'packed.json',placement/'placement.json',a.cst.resolve(),a.sdc.resolve())}
    def save():result['wall_seconds']=time.monotonic()-start;(out/'manifest.json').write_text(json.dumps(result,indent=2))
    def run(label,script,args):
        reserve(ROOT,500_000_000);remaining=deadline-time.monotonic()
        if remaining<=0:raise TimeoutError('Complete checkpoint deadline exhausted')
        cmd=[sys.executable,str(ROOT/script),*map(str,args)];row=dict(stage=label,status='running',command=cmd);result['stages'].append(row);save()
        with (out/(label+'.log')).open('w') as log:
            proc=subprocess.Popen(cmd,cwd=ROOT,stdout=log,stderr=subprocess.STDOUT,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            try:code=proc.wait(timeout=remaining)
            except subprocess.TimeoutExpired:stop_owned_process_tree(proc);row['status']='timeout';raise
        row.update(status='passed' if code==0 else 'failed',returncode=code);save()
        if code:raise RuntimeError('Checkpoint stage failed: '+label)
    save()
    try:
        design=json.loads((placement/'packed.json').read_text());placed=json.loads((placement/'placement.json').read_text())
        from ownpr_place import constraints
        contract=constraints(design['modules']['top'],a.cst.read_text(),a.sdc.read_text())
        if contract['clock_routing']:raise ValueError('Dedicated clock checkpoint requires an AUTO clock policy')
        hard_audit=None;hard_args=[]
        if placed.get('hard_clusters'):
            from ownpr_hard_architecture import HardArchitecture
            run('hard_bindings','experiments/native_hard_placement_probe.py',['--placement',placement,'--out',out/'hard_bindings'])
            hard_audit=HardArchitecture().selected_bindings(design,placed,out/'hard_bindings');hard_args=['--hard-bindings',out/'hard_bindings']
        graph=RoutingGraph(Device(),ROOT/'results/native_rrg_001');nets,_=bind(design,placed,graph,hard_bindings=hard_audit);clocks=[n for n in nets if n.get('clock')]
        if len(clocks)!=1:raise ValueError('Checkpoint backend requires one clock domain')
        def point(n):r,c=graph[n];return [r+1,c+1,n%graph.n]
        clock=clocks[0]
        if a.clock_tree:
            tree=json.loads(a.clock_tree.read_text());cg=ClockGraph.for_tree(tree)
            if tree['source']!=point(clock['source']) or {tuple(p[0]) for p in tree['paths_sink_to_source']}!={tuple(point(n)) for n in clock['sinks']}:raise ValueError('Reused clock tree changed physical source/sinks')
            if cg.audit_paths(tree['paths_sink_to_source'])['status']!='clock_paths_match':raise ValueError('Reused clock topology invalid')
            clock_capture=a.clock_capture.resolve();result['reused_clock_tree_sha256']=sha(a.clock_tree)
        else:
            if a.anchor_clock_tree:
                anchor=json.loads(a.anchor_clock_tree.read_text());cg=ClockGraph.for_tree(anchor);tree=cg.retarget(anchor,point(clock['source']),[point(n) for n in clock['sinks']]);result['anchor_clock_tree_sha256']=sha(a.anchor_clock_tree)
            else:
                cg=ClockGraph(ROOT/'results/native_clock_inventory_005');tree=cg.candidate(point(clock['source']),[point(n) for n in clock['sinks']],'AUTO')
            clock_capture=out/'clock_encoding'
        _,info=ClockChoices(graph.device,cg).requirements(tree)
        if info['conflicts']:raise ValueError('Initial clock configuration conflicts')
        if a.clock_tree:
            import shutil
            shutil.copyfile(a.clock_tree,out/'clock_tree.json')
        else:
            (out/'clock_tree.json').write_text(json.dumps(tree,indent=2))
            run('clock_encoding','experiments/native_clock_encoding_probe.py',['--tree',out/'clock_tree.json','--compact','--out',clock_capture])
        remaining=int(deadline-time.monotonic())
        if remaining<120:raise TimeoutError('Insufficient time for routing and complete export')
        policy=json.loads((a.reuse_routes/'manifest.json').read_text()) if a.reuse_routes else {}
        result['inherited_route_policy']={k:policy.get(k) for k in ('search_bias','root_arrival_weight','guide_edge_delay_ns_surrogate','guided_corridor')}
        effective=routing_policy(policy,a.search_bias,a.root_arrival_weight,a.guide_edge_delay,a.guided_corridor);result['effective_route_policy']=effective
        route_args=['--search-bias',effective['search_bias'],'--root-arrival-weight',effective['root_arrival_weight'],'--guide-edge-delay',effective['guide_edge_delay_ns_surrogate']]
        if a.pin_feedback:route_args+=['--pin-feedback',a.pin_feedback.resolve(),'--feedback-sta',a.feedback_sta.resolve(),'--feedback-placement',a.feedback_placement.resolve()]
        run('route','scripts/run_ownpr_timing_route.py',['--placement',placement,'--clock-tree',out/'clock_tree.json','--timeout',min(a.route_seconds,remaining-90),'--out',out/'route']+route_args+hard_args+(['--guided-corridor'] if effective['guided_corridor'] else [])+(['--reuse-routes',a.reuse_routes.resolve(),'--reuse-branches'] if a.reuse_routes else []))
        run('export','scripts/export_ownpr.py',['--placement',placement,'--routes',out/'route','--clock-capture',clock_capture,'--cst',a.cst.resolve(),
            '--sdc',a.sdc.resolve(),'--io-reference',a.io_reference.resolve(),'--seconds',max(1,int(deadline-time.monotonic())-1),'--out',out/'export'])
        report=json.loads((out/'export/manifest.json').read_text());result.update(status='checkpoint_handoff_complete',metrics=report['metrics'],selected=dict(
            placement=str(placement),routes=str(out/'route'),wire=str(out/'export/wire'),sta=str(out/'export/native'),clock_capture=str(clock_capture)))
    except Exception as e:
        result.update(status='timeout' if isinstance(e,(TimeoutError,subprocess.TimeoutExpired)) else 'failed',error=str(e));save();raise
    save();print(json.dumps({k:result[k] for k in ('status','wall_seconds','metrics')},indent=2))


if __name__=='__main__':main()
