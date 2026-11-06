"""Own delay-informed router using only native device/timing data queries."""
import argparse,json,os,shutil,subprocess,sys,time
from pathlib import Path
from gowin_probe import ROOT,prepare
from ownpr_image_storage import sha,load_json,available
from capture_runtime_tables import EXPECTED


def worker(out,placement,timeout,clock_tree=None,reuse_routes=None,reuse_branches=False,hard_bindings=None,clock_inventory=None,resume_checkpoint=None,search_bias=1.,root_arrival_weight=1.,guided_corridor=False,guide_edge_delay=1.,resume_negotiation=False,pin_feedback=None,feedback_sta=None,feedback_placement=None):
    from native_device_session import NativeDeviceSession
    from ownpr_device import Device,RoutingGraph
    from ownpr_timing_graph import TimingRoutingGraph
    from ownpr_route_design import bind
    from ownpr_router import route,validate,RoutingTimeout
    start=time.monotonic();native=NativeDeviceSession(out)
    graph=TimingRoutingGraph(Device(),ROOT/'results/native_rrg_001',native)
    graph.heuristic_scale*=search_bias
    graph.root_arrival_weight=root_arrival_weight;graph.guided_corridor=guided_corridor;graph.guide_edge_delay=guide_edge_delay
    packed=placement/'packed.json';placed=placement/'placement.json'
    design=json.loads(packed.read_text());places=json.loads(placed.read_text());hard_audit=None
    if hard_bindings:
        from ownpr_hard_architecture import HardArchitecture
        hard_audit=HardArchitecture().selected_bindings(design,places,Path(hard_bindings));(out/'hard_endpoint_audit.json').write_text(json.dumps(hard_audit,indent=2))
    receivers=[];nets,static=bind(design,places,graph,hard_bindings=hard_audit,logical_sinks=receivers)
    (out/'all_nets.json').write_text(json.dumps(nets,indent=2));clock_partition=None;reserved=set()
    if clock_tree:
        from ownpr_clock_partition import partition
        from ownpr_clock_graph import ClockGraph
        clock_model=ClockGraph(Path(clock_inventory)) if clock_inventory else None
        nets,clock_partition=partition(graph,nets,json.loads(Path(clock_tree).read_text()),clock_model)
        static.append(dict(kind='dedicated_clock',configuration_mode_verified=False,**clock_partition))
        shutil.copyfile(clock_tree,out/'clock_tree.json')
        for entry in json.loads(Path(clock_tree).read_text())['parents']:
            r,c,i=entry['node']
            if r>=1 and c>=1 and i in graph.device.names:reserved.add(graph.node(r-1,c-1,graph.device.names[i]))
    from ownpr_ioreg_macros import reserved_points
    for r,c,i in reserved_points(design,places):reserved.add(graph.node(r-1,c-1,graph.device.names[i]))
    (out/'nets.json').write_text(json.dumps(nets,indent=2))
    (out/'static_connections.json').write_text(json.dumps(static,indent=2))
    meta=dict(status='routing',netlist_sha256=sha(packed),placement_sha256=sha(placed),vendor_pnr_executed=False,
        open_chip_database_used=False,net_count=len(nets),timing_signoff=False,bitstream_generated=False,
        static_configuration_pending=[p for p in static if not p.get('configuration_mode_verified')],
        native_source_sha256=native.source_sha256,
        timing_cost_model='Unloaded native PIP estimate + maximum intrinsic-entry path; whole-tree load remains to be recomputed.',
        existing_tree_arrival_in_search=True,reserved_clock_and_output_nodes=len(reserved))
    meta['search_bias']=search_bias;meta['heuristic_scale']=graph.heuristic_scale
    meta['root_arrival_weight']=root_arrival_weight;meta['guided_corridor']=guided_corridor;meta['guide_edge_delay_ns_surrogate']=guide_edge_delay
    if clock_partition:meta['dedicated_clock']=dict(clock_partition,tree_sha256=sha(out/'clock_tree.json'))
    if hard_audit:
        meta['hard_endpoint_audit_sha256']=sha(out/'hard_endpoint_audit.json');meta['hard_cells']=len(hard_audit['cells']);meta['complete_design_connectivity_verified']=False
    if places.get('arithmetic_clusters'):
        from ownpr_carry_macros import validate as validate_carry
        meta['carry_capacity_audit']=validate_carry(design,places,graph.device)
        meta['complete_design_connectivity_verified']=False
    if places.get('mux_clusters'):
        from ownpr_mux_macros import validate as validate_mux
        meta['wide_mux_capacity_audit']=validate_mux(design,places,graph.device)
        meta['complete_design_connectivity_verified']=False
    if places.get('ioreg_clusters'):
        from ownpr_ioreg_macros import validate as validate_ioreg
        meta['ioreg_capacity_audit']=validate_ioreg(design,places,graph.device)
        meta['complete_design_connectivity_verified']=False
    (out/'manifest.json').write_text(json.dumps(meta,indent=2))
    if pin_feedback:
        from ownpr_pin_feedback import load
        from ownpr_feedback_transport import transport
        source=Path(feedback_placement);sta=Path(feedback_sta);sm=load_json(sta/'manifest.json')
        if sm['packed_sha256']!=sha(source/'packed.json') or sm['placement_sha256']!=sha(source/'placement.json') or sm['vendor_placement_executed'] or sm['vendor_routing_executed']:raise ValueError('Historical feedback implementation differs')
        old_design=load_json(source/'packed.json');old_places=load_json(source/'placement.json')
        if places['fixed_io']!=old_places['fixed_io'] or design['modules']['top']['ports']!=old_design['modules']['top']['ports']:raise ValueError('Historical feedback changes fixed I/O contract')
        budgets,info=load(pin_feedback,sta)
        nets,policy=transport(nets,receivers,old_design,design,budgets)
        (out/'search_weights.json').write_text(json.dumps(dict(capture=info,policy=policy),sort_keys=True,separators=(',',':')))
        meta['pin_feedback']=info;meta['search_weights_sha256']=sha(out/'search_weights.json');meta['historical_priority_policy']={k:v for k,v in policy.items() if k!='receivers'}
    def progress(row):
        with (out/'progress.jsonl').open('a') as log:log.write(json.dumps(row)+'\n')
        if row['event'] in ('net_done','iteration_done'):print(json.dumps(row),flush=True)
    try:
        from ownpr_router import source_frontier_audit
        meta['source_frontier_audit']=source_frontier_audit(nets,lambda node:RoutingGraph.downhill(graph,node))
        result=None;checkpoint_seeds=None;warm_checkpoint=None;incremental_partial=None
        if resume_checkpoint:
            from ownpr_route_checkpoint import safe_seeds,negotiation_trees
            source=Path(resume_checkpoint);prior=json.loads((source/'manifest.json').read_text())
            if prior.get('vendor_pnr_executed') is not False or any(prior.get(k)!=meta[k] for k in ('netlist_sha256','placement_sha256')):raise ValueError('Checkpoint belongs to another implementation')
            if prior.get('search_weights_sha256')!=meta.get('search_weights_sha256'):raise ValueError('Checkpoint historical timing priorities changed')
            for filename in ('nets.json','all_nets.json','clock_tree.json'):
                if available(out/filename)!=available(source/filename) or available(out/filename) and sha(out/filename)!=sha(source/filename):raise ValueError('Checkpoint network/clock contract changed')
            file=source/'partial_routes.json'
            if sha(file)!=prior.get('partial_routes_sha256'):raise ValueError('Checkpoint file changed')
            checkpoint=json.loads(file.read_text())
            if resume_negotiation:
                _,check=negotiation_trees(graph,nets,checkpoint,reserved);warm_checkpoint=checkpoint
            else:checkpoint_seeds,check=safe_seeds(graph,nets,checkpoint,reserved)
            meta['checkpoint_resume']=dict(source=str(source),source_sha256=sha(file),audit=check)
            meta['prior_cumulative_route_seconds']=prior.get('cumulative_route_seconds',prior['total_wall_seconds'])
        if reuse_routes:
            from ownpr_incremental_routes import partition as incremental_partition,retained_branches,audit_retention
            source=Path(reuse_routes);prior=json.loads((source/'manifest.json').read_text())
            if prior['status']!='routed' or prior['vendor_pnr_executed']:raise ValueError('Incremental reuse requires own completed routing')
            old_nets=load_json(source/'nets.json');old_routes=load_json(source/'routes.json')
            changed,frozen,blocked,inc=incremental_partition(graph,old_nets,old_routes,nets,reserved)
            inc['source_sha256']={n:sha(source/n) for n in ('manifest.json','nets.json','routes.json')};meta['incremental']=inc
            branch_checkpoint=None
            if reuse_branches:
                seeds,branch_info=retained_branches(graph,old_nets,old_routes,changed,nets,blocked);inc['branches']=branch_info
                if seeds:
                    try:
                        delta=route(graph,changed,max_iterations=12,timeout=max(1,min(60,(timeout-(time.monotonic()-start))*.4)),
                            max_expansions=100000,progress=progress,blocked_nodes=blocked,initial_trees=seeds)
                        if delta['status']=='routed':
                            retention=audit_retention(graph,seeds,delta);result=dict(delta,nets={**frozen,**delta['nets']});validate(graph,nets,result)
                            branch_info.update(audit=retention,fallback_to_net_routing=False);inc['fallback_full_routing']=False
                        else:
                            incremental_partial=delta;branch_info.update(fallback_to_net_routing=True,failure=dict(status=delta['status'],failed=delta.get('failed')))
                    except TimeoutError as error:
                        if isinstance(error,RoutingTimeout):incremental_partial=error.checkpoint
                        branch_info.update(fallback_to_net_routing=True,failure=str(error))
                    if result is None:
                        if incremental_partial is not None:
                            from ownpr_incremental_routes import mutable_fallback
                            branch_checkpoint,admission=mutable_fallback(graph,changed,{},incremental_partial,blocked)
                            branch_info.update(mutable_fallback=admission,fallback_discards_completed_trees=False)
                        print('BRANCH_REUSE_FALLBACK',json.dumps(branch_info),flush=True)
            try:
                if result is not None:delta=None
                else:
                    delta=route(graph,changed,max_iterations=12,timeout=max(1,min(90,(timeout-(time.monotonic()-start))*.6)),max_expansions=100000,progress=progress,blocked_nodes=blocked,warm_checkpoint=branch_checkpoint)
                if delta is not None and delta['status']=='routed':
                    result=dict(delta,nets={**frozen,**delta['nets']});validate(graph,nets,result);inc['fallback_full_routing']=False
                elif delta is not None:
                    incremental_partial=delta;inc.update(fallback_full_routing=True,failure=dict(status=delta['status'],failed=delta.get('failed')))
            except TimeoutError as error:
                if isinstance(error,RoutingTimeout):incremental_partial=error.checkpoint
                inc.update(fallback_full_routing=True,failure=str(error))
            if result is None:
                if incremental_partial is not None:
                    from ownpr_incremental_routes import mutable_fallback
                    warm_checkpoint,admission=mutable_fallback(graph,nets,frozen,incremental_partial,reserved)
                    checkpoint_path=out/'incremental_fallback_checkpoint.json';checkpoint_path.write_text(json.dumps(warm_checkpoint))
                    inc.update(mutable_fallback=admission,fallback_discards_existing_trees=False,mutable_checkpoint_sha256=sha(checkpoint_path))
                else:inc['fallback_discards_existing_trees']=True
                print('INCREMENTAL_FALLBACK',json.dumps(inc),flush=True)
        if result is None:
            result=route(graph,nets,max_iterations=20,timeout=max(1,timeout-(time.monotonic()-start)),max_expansions=100000,progress=progress,blocked_nodes=reserved,initial_trees=checkpoint_seeds,warm_checkpoint=warm_checkpoint)
        (out/'routes.json').write_text(json.dumps(result));meta['status']=result['status'];meta['iterations']=result['iterations']
        if result['status']=='routed':meta['connectivity_audit']=validate(graph,nets,result)
    except Exception as error:
        meta.update(status='failed',error=repr(error))
        if isinstance(error,RoutingTimeout):
            (out/'partial_routes.json').write_text(json.dumps(error.checkpoint));meta['partial_routes_sha256']=sha(out/'partial_routes.json')
            meta['checkpoint_nets']=len(error.checkpoint['nets'])
    meta.update(wall_seconds=time.monotonic()-start,estimated_pips=graph.cost_statistics(),
        scope='Own ordinary-wire routing with device delay estimates. Complete physical-mode, clock and timing signoff remains pending.')
    (out/'manifest.json').write_text(json.dumps(meta,indent=2));print(json.dumps({k:meta[k] for k in ('status','error','wall_seconds','net_count','estimated_pips') if k in meta},indent=2),flush=True)
    if meta['status']!='routed':raise RuntimeError('Own timing-informed routing did not complete')


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--placement',type=Path,required=True);ap.add_argument('--out',type=Path,required=True)
    ap.add_argument('--timeout',type=int,default=600);ap.add_argument('--worker',action='store_true');ap.add_argument('--clock-tree',type=Path);ap.add_argument('--reuse-routes',type=Path)
    ap.add_argument('--hard-bindings',type=Path);ap.add_argument('--clock-inventory',type=Path)
    ap.add_argument('--pin-feedback',type=Path);ap.add_argument('--feedback-sta',type=Path);ap.add_argument('--feedback-placement',type=Path)
    ap.add_argument('--resume-checkpoint',type=Path);ap.add_argument('--resume-negotiation',action='store_true',help='Reuse checked trees as mutable congestion state, rather than frozen branches');ap.add_argument('--search-bias',type=float,default=1.)
    ap.add_argument('--root-arrival-weight',type=float,default=1.);ap.add_argument('--guided-corridor',action='store_true');ap.add_argument('--guide-edge-delay',type=float,default=1.,help='Coarse geometric edge cost in ns units; final search still uses native edge delays')
    ap.add_argument('--reuse-branches',action='store_true');args=ap.parse_args()
    if sum(v is not None for v in (args.pin_feedback,args.feedback_sta,args.feedback_placement)) not in (0,3):ap.error('Historical feedback needs capture, STA and source placement together')
    if args.reuse_branches and not args.reuse_routes:ap.error('--reuse-branches requires --reuse-routes')
    if args.resume_negotiation and not args.resume_checkpoint:ap.error('--resume-negotiation requires --resume-checkpoint')
    if args.resume_checkpoint and args.reuse_routes:ap.error('Checkpoint resume and completed-route reuse are separate modes')
    if not 1<=args.search_bias<=30:ap.error('Search bias must be finite and in [1,30]')
    if not 0<=args.root_arrival_weight<=1:ap.error('Root arrival weight must be finite and in [0,1]')
    if not 0<args.guide_edge_delay<=1:ap.error('Guide edge delay must be finite and in (0,1]')
    out=args.out.resolve();placement=args.placement.resolve()
    if not 0<args.timeout<=3500:raise ValueError('Complete attempt must fit within one hour')
    if args.worker:return worker(out,placement,args.timeout,args.clock_tree,args.reuse_routes,args.reuse_branches,args.hard_bindings,args.clock_inventory,args.resume_checkpoint,args.search_bias,args.root_arrival_weight,args.guided_corridor,args.guide_edge_delay,args.resume_negotiation,args.pin_feedback,args.feedback_sta,args.feedback_placement)
    prior_seconds=0.
    if args.resume_checkpoint:
        previous=json.loads((args.resume_checkpoint/'manifest.json').read_text());prior_seconds=previous.get('cumulative_route_seconds',previous['total_wall_seconds'])
        if prior_seconds+args.timeout+30>3000:raise ValueError('Cumulative routing budget leaves insufficient time for complete checks/export within one hour')
    from workspace_budget import reserve
    reserve(ROOT,500_000_000)
    prepare(out);shutil.copyfile(__file__,out/'runner_source.py.txt');python=ROOT/'binary_audit/flow/runtime/bin/python.exe'
    sources=out/'sources';sources.mkdir()
    for name in ('ownpr_router.py','ownpr_incremental_routes.py','ownpr_timing_graph.py','native_device_session.py','ownpr_device.py','ownpr_route_design.py','ownpr_carry_macros.py','ownpr_clock_partition.py','ownpr_alu_architecture.py','ownpr_alu_configuration.py','ownpr_mux_architecture.py','ownpr_mux_macros.py','ownpr_ioreg_macros.py','ownpr_ioreg_configuration.py'):
        shutil.copyfile(ROOT/'scripts'/name,sources/name)
    shutil.copyfile(ROOT/'scripts/ownpr_route_checkpoint.py',sources/'ownpr_route_checkpoint.py')
    shutil.copyfile(ROOT/'scripts/ownpr_carry_lut_sharing.py',sources/'ownpr_carry_lut_sharing.py')
    shutil.copyfile(ROOT/'scripts/ownpr_wide_lut_sharing.py',sources/'ownpr_wide_lut_sharing.py')
    if args.pin_feedback:
        for name in ('ownpr_feedback_transport.py','ownpr_pin_feedback.py'):shutil.copyfile(ROOT/'scripts'/name,sources/name)
    if args.hard_bindings:
        for name in ('ownpr_hard_architecture.py','ownpr_hard_timing_model.py'):shutil.copyfile(ROOT/'scripts'/name,sources/name)
    start=time.monotonic();rc=None;expired=False
    with (out/'worker.log').open('w') as log:
        try:rc=subprocess.run([str(python),str(Path(__file__).resolve()),'--worker','--placement',str(placement),'--out',str(out),'--timeout',str(args.timeout)]+(['--clock-tree',str(args.clock_tree.resolve())] if args.clock_tree else [])+(['--reuse-routes',str(args.reuse_routes.resolve())] if args.reuse_routes else [])+(['--reuse-branches'] if args.reuse_branches else [])
            +(['--hard-bindings',str(args.hard_bindings.resolve())] if args.hard_bindings else [])+(['--clock-inventory',str(args.clock_inventory.resolve())] if args.clock_inventory else [])
            +(['--resume-checkpoint',str(args.resume_checkpoint.resolve())] if args.resume_checkpoint else [])+['--search-bias',str(args.search_bias),'--root-arrival-weight',str(args.root_arrival_weight),'--guide-edge-delay',str(args.guide_edge_delay)]+(['--guided-corridor'] if args.guided_corridor else [])+(['--resume-negotiation'] if args.resume_negotiation else [])
            +(['--pin-feedback',str(args.pin_feedback.resolve()),'--feedback-sta',str(args.feedback_sta.resolve()),'--feedback-placement',str(args.feedback_placement.resolve())] if args.pin_feedback else []),
            cwd=python.parent,env=dict(os.environ,PYTHONHOME=sys.base_prefix),stdout=log,stderr=subprocess.STDOUT,timeout=args.timeout+30).returncode
        except subprocess.TimeoutExpired:expired=True
    manifest=out/'manifest.json';meta=json.loads(manifest.read_text()) if manifest.exists() else {}
    if rc!=0:meta['status']='failed'
    meta.update(returncode=rc,timeout=expired,total_wall_seconds=time.monotonic()-start,dll_sha256=EXPECTED,
        vendor_pnr_executed=False,open_chip_database_used=False,timing_signoff=False)
    meta['cumulative_route_seconds']=prior_seconds+meta['total_wall_seconds'];meta['complete_attempt_time_verified']=False
    meta['source_sha256']={p.name:sha(p) for p in sources.iterdir()}
    manifest.write_text(json.dumps(meta,indent=2));print(json.dumps({k:meta[k] for k in ('status','error','total_wall_seconds','net_count','estimated_pips') if k in meta},indent=2))
    if meta.get('status')!='routed':raise SystemExit(1)


if __name__=='__main__':main()
