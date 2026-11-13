"""Native-pin-guided multi-net negotiated routing on an audited own checkpoint.

Unselected nets remain mutable if congestion reaches them. Canonical nets,
placement and clocks are unchanged. This is a candidate until full export STA.
"""
import argparse,json,os,shutil,subprocess,sys,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
from ownpr_image_storage import load_json,sha,copy_original
from workspace_budget import reserve

def worker(a):
    from native_device_session import NativeDeviceSession
    native=NativeDeviceSession(a.out)
    from ownpr_device import Device
    from ownpr_timing_graph import TimingRoutingGraph
    from ownpr_wide_ff_packing import configure_graph
    from ownpr_critical_tree_rebuild import verify_feedback
    from ownpr_pin_feedback import load,physical_budgets,routing_priorities
    from ownpr_router import route,validate,RoutingTimeout
    started=time.monotonic();meta,sm,_=verify_feedback(a.placement,a.routes,a.sta)
    design=load_json(a.placement/'packed.json');placed=load_json(a.placement/'placement.json')
    graph=TimingRoutingGraph(Device(),ROOT/'results/native_rrg_001',native);configure_graph(graph,design,placed)
    graph.heuristic_scale=.03;graph.guided_corridor=True;graph.guide_edge_delay=.05
    nets=load_json(a.routes/'nets.json');original=load_json(a.routes/'routes.json')
    logical,info=load(a.pin_feedback,a.sta);budgets=physical_budgets(graph,load_json(a.sta/'sta_endpoint_audit.json'),logical)
    weighted,policy=routing_priorities(nets,budgets,a.window_ns)
    ranked=sorted([n for n in weighted if not n.get('clock') and 'constant_value' not in n and n['criticality']>=.95],key=lambda n:(-n['criticality'],-len(n['sinks']),n['name']))
    forced=[n['name'] for n in ranked[:a.count]]
    if not forced:raise ValueError('No near-critical networks selected')
    reserved=set()
    for row in load_json(a.routes/'clock_tree.json')['parents']:
        r,c,i=row['node']
        if min(r,c)>=1 and i in graph.device.names:reserved.add(graph.node(r-1,c-1,graph.device.names[i]))
    from ownpr_ioreg_macros import reserved_points
    reserved.update(graph.node(r,c,graph.device.names[i]) for r,c,i in reserved_points(design,placed))
    validate(graph,nets,original)
    warm=dict(status='incomplete_route_checkpoint',source_status='routed',purpose='mutable physical optimization seed',iterations=[],history=[],
        nets={n['name']:dict(original['nets'][n['name']],retained_sinks=n['sinks']) for n in nets})
    source_names=('nets.json','all_nets.json','clock_tree.json','static_connections.json','hard_endpoint_audit.json')
    for name in source_names:
        if (a.routes/name).exists():copy_original(a.routes/name,a.out/name)
    (a.out/'search_weights.json').write_text(json.dumps(dict(forced_nets=forced,policy=policy,pin_capture=info,nets=[dict(name=n['name'],criticality=n['criticality'],sinks=n.get('sink_criticalities',{})) for n in weighted]),separators=(',',':')))
    def progress(row):
        with (a.out/'progress.jsonl').open('a') as log:log.write(json.dumps(row)+'\n')
        if row['event'] in ('net_done','iteration_done'):print(json.dumps(row),flush=True)
    keys=('netlist_sha256','placement_sha256','vendor_pnr_executed','open_chip_database_used','net_count','native_source_sha256','dll_sha256','dedicated_clock','carry_capacity_audit','wide_mux_capacity_audit','static_configuration_pending','hard_endpoint_audit_sha256','hard_cells')
    result={k:meta[k] for k in keys if k in meta}
    result.update(status='running',forced_nets=forced,pin_feedback=info,routing_policy=policy,source_routes_sha256=sha(a.routes/'routes.json'),source_sta_sha256=sha(a.sta/'probe.tr'),timing_signoff=False,full_design_sta_required=True)
    try:
        routed=route(graph,weighted,max_iterations=12,timeout=max(1,a.seconds-(time.monotonic()-started)),max_expansions=100000,
            blocked_nodes=reserved,warm_checkpoint=warm,force_reroute=forced,progress=progress)
        (a.out/'routes.json').write_text(json.dumps(routed));result['status']=routed['status'];result['iterations']=routed['iterations']
        if routed['status']=='routed':
            result['connectivity_audit']=validate(graph,nets,routed)
            result['changed_nets']=[n for n in original['nets'] if any(original['nets'][n][k]!=routed['nets'][n][k] for k in ('pips','nodes'))]
    except RoutingTimeout as error:
        (a.out/'partial_routes.json').write_text(json.dumps(error.checkpoint));result.update(status='timeout',error=str(error))
    result.update(wall_seconds=time.monotonic()-started,search_weight_sha256=sha(a.out/'search_weights.json'),estimated_pips=graph.cost_statistics())
    (a.out/'manifest.json').write_text(json.dumps(result,indent=2))
    if result['status']!='routed':raise RuntimeError('Multi-net timing route not completed')

def main():
    p=argparse.ArgumentParser(description=__doc__)
    for n in ('placement','routes','sta','pin-feedback','out'):p.add_argument('--'+n,type=Path,required=True)
    p.add_argument('--count',type=int,default=8);p.add_argument('--seconds',type=int,default=300);p.add_argument('--window-ns',type=float,default=4.);p.add_argument('--worker',action='store_true');a=p.parse_args()
    if not 1<=a.count<=64 or not 1<=a.seconds<=1200:p.error('Invalid bounded route attempt')
    for n in ('placement','routes','sta','pin_feedback','out'):setattr(a,n,getattr(a,n).resolve())
    if a.worker:return worker(a)
    reserve(ROOT,300_000_000)
    from gowin_probe import prepare
    prepare(a.out);source=a.out/'sources';source.mkdir()
    for name in ('run_ownpr_pin_route.py','ownpr_router.py','ownpr_pin_feedback.py','ownpr_route_checkpoint.py','ownpr_timing_graph.py','ownpr_critical_tree_rebuild.py'):
        shutil.copyfile(ROOT/'scripts'/name,source/name)
    python=ROOT/'binary_audit/flow/runtime/bin/python.exe';start=time.monotonic();rc=None;expired=False
    cmd=[str(python),str(Path(__file__).resolve()),'--worker','--count',str(a.count),'--seconds',str(a.seconds),'--window-ns',str(a.window_ns)]
    for n in ('placement','routes','sta','pin_feedback','out'):cmd+=['--'+n.replace('_','-'),str(getattr(a,n))]
    with (a.out/'worker.log').open('w') as log:
        try:rc=subprocess.run(cmd,cwd=ROOT,env=dict(os.environ,PYTHONHOME=sys.base_prefix),stdout=log,stderr=subprocess.STDOUT,timeout=a.seconds+60,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0)).returncode
        except subprocess.TimeoutExpired:expired=True
    m=load_json(a.out/'manifest.json') if (a.out/'manifest.json').exists() else {}
    if rc!=0:m['status']='timeout' if expired else 'failed'
    m.update(returncode=rc,timeout=expired,total_wall_seconds=time.monotonic()-start,producer_source_sha256={f.name:sha(f) for f in source.iterdir()},vendor_pnr_executed=False)
    (a.out/'manifest.json').write_text(json.dumps(m,indent=2)+'\n');print(dict(status=m.get('status'),changed_nets=len(m.get('changed_nets',[])),total_wall_seconds=m['total_wall_seconds']))
    if m['status']!='routed':raise SystemExit(1)

if __name__=='__main__':main()
