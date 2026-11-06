"""Measured whole-tree, coupled-budget admission of mixed carry/LUT moves."""
import argparse,copy,json,os,shutil,subprocess,sys,time
from pathlib import Path
from gowin_probe import ROOT,prepare
from ownpr_image_storage import load_json,sha


def worker(a):
    from native_device_session import NativeDeviceSession
    start=time.monotonic();deadline=start+a.seconds;native=NativeDeviceSession(a.out)
    from ownpr_device import Device,RoutingGraph
    from ownpr_timing_graph import TimingRoutingGraph
    from ownpr_cell_timing import NativeCellTiming
    from ownpr_expand_routes import expand
    from ownpr_route_design import bind
    from ownpr_clock_graph import ClockGraph
    from ownpr_clock_partition import partition
    from ownpr_router import route,validate
    from ownpr_incremental_routes import signature,retained_branches
    from ownpr_source_reconnect import reconnect_source
    from ownpr_pack_carry_neighbors import propose,native_neighbor_ranking
    from ownpr_carry_lut_sharing import calibration
    from ownpr_carry_macros import validate as carry_validate
    from ownpr_pin_feedback import load,physical_budgets,placement_priorities
    from ownpr_budgeted_restore import checked_wire
    from ownpr_wire_budget import delays,WireDependency
    from ownpr_lut_move_budget import coupled_budget,combined_budget
    from ownpr_critical_tree_rebuild import verify_feedback
    verify_feedback(a.placement,a.routes,a.sta)
    sys.path.insert(0,str(ROOT/'experiments'))
    from analyze_ownpr_critical_wires import analyze
    design=load_json(a.placement/'packed.json');places=load_json(a.placement/'placement.json');cells=design['modules']['top']['cells']
    graph=TimingRoutingGraph(Device(),ROOT/'results/native_rrg_001',native);library=NativeCellTiming()
    graph.heuristic_scale=.03;graph.guided_corridor=True;graph.guide_edge_delay=.05
    oldnets=load_json(a.routes/'nets.json');byname={n['name']:n for n in oldnets};oldroutes=load_json(a.routes/'routes.json')
    oldwire=checked_wire(a.wire,a.routes);logical,pin_info=load(a.pin_feedback,a.sta)
    hard=load_json(a.routes/'hard_endpoint_audit.json') if places.get('hard_clusters') else None
    if hard and load_json(a.routes/'manifest.json')['hard_endpoint_audit_sha256']!=sha(a.routes/'hard_endpoint_audit.json'):raise ValueError('Hard endpoint capture changed')
    clock=load_json(a.routes/'clock_tree.json');clock_model=ClockGraph.for_tree(clock)
    reserved={graph.node(r-1,c-1,graph.device.names[i]) for row in clock['parents'] for r,c,i in [row['node']] if min(r,c)>=1 and i in graph.device.names}
    from ownpr_ioreg_macros import reserved_points
    reserved.update(graph.node(r-1,c-1,graph.device.names[i]) for r,c,i in reserved_points(design,places))
    def binding(layout):
        allnets,static=bind(design,layout,graph,hard_bindings=hard)
        return partition(graph,allnets,clock,clock_model)[0],allnets,static
    initial,_,_=binding(places)
    if {n['name']:signature(n) for n in initial}!={n['name']:signature(n) for n in oldnets}:raise ValueError('Baseline binding differs')
    budgets=physical_budgets(graph,load_json(a.sta/'sta_endpoint_audit.json'),logical)
    ranking=analyze(a.sta,a.placement,a.routes,128);priorities=placement_priorities(design,logical)
    enumeration=dict(scope='First 128 reported endpoint paths')
    if a.native_neighbors:
        ranking,enumeration=native_neighbor_ranking(design,ranking,logical,{n:r['max_setup_wire_ns'] for n,r in oldwire['nets'].items()})
    _,suggested=propose(design,places,ranking,priorities,count=len(cells) if a.only_lut else a.count,require_distance_gain=False,independent=True)
    if a.only_lut:
        suggested=[r for r in suggested if r['lut']==a.only_lut]
        if not suggested:raise ValueError('Diagnostic LUT has no calibrated neighbor candidate')
    enumeration.update(independent_proposals=True,max_sites_per_lut=2,proposed_sites=len(suggested))
    occupied={(r['row'],r['column'],r['slot']) for r in places['clusters'].values()}
    def measure(net,record):
        physical=expand(graph,[net],dict(nets={net['name']:record}),native.fanouts)['nets'][net['name']]
        parents={tuple(p['node']):tuple(p['parent']) if p['parent'] else None for p in physical['parents']}
        with native.parent_tree(parents):
            values={p:(native.delay(p,1),native.delay(p,2)) for p in parents}
            return {s['resource']:[sum(values[tuple(p)][selector][i] for p in s['path_sink_to_source']) for selector,i in ((0,2),(0,3),(1,0),(1,1))] for s in physical['sinks']}
    trials=[];candidates=[]
    for suggestion in suggested:
        name=suggestion['lut'];oldsite=places['clusters'][name];cell=cells[name];output='net_'+str(cell['connections']['F'][0])
        for slot in (6,7):
            if time.monotonic()+25>deadline:break
            site=(suggestion['row'],suggestion['column'],slot)
            if site in occupied:continue
            info=dict(lut=name,old_site=[oldsite[k] for k in ('row','column','slot')],new_site=list(site),worst_path_slack_ns=suggestion['slack_ns'])
            try:
                layout=copy.deepcopy(places);layout['clusters'][name].update(row=site[0],column=site[1],slot=slot)
                layout['mixed_carry_lut_calibration']=calibration()['capture_sha256'];carry_validate(design,layout,graph.device)
                newnets,_,_=binding(layout);newby={n['name']:n for n in newnets}
                if set(newby)!=set(byname):raise ValueError('Move changes ordinary net identities')
                affected={n for n in byname if signature(byname[n])!=signature(newby[n])}
                if output not in affected or any(byname[n].get('clock') or 'constant_value' in byname[n] for n in affected):raise ValueError('Unsupported moved clock/constant/output contract')
                input_pins=[]
                for pin,bits in cell['connections'].items():
                    n='net_'+str(bits[0])
                    if pin.startswith('I') and n in affected:
                        index=int(pin[1:]);before=graph.node(oldsite['row'],oldsite['column'],'ABCD'[index]+str(oldsite['slot']));after=graph.node(site[0],site[1],'ABCD'[index]+str(slot))
                        input_pins.append(dict(net=n,pin=pin,index=index,old_node=before,new_node=after))
                if not input_pins or affected!={p['net'] for p in input_pins}|{output}:raise ValueError('Unmodeled incident net changes')
                if any(p['net']==output for p in input_pins):raise ValueError('Combinational self feedback unsupported')
                owners={node:n for n,r in oldroutes['nets'].items() if n!=output for node in r['nodes']};owners.update({node:'__reserved__' for node in reserved})
                changed={};measured={}
                for n in sorted(affected-{output}):
                    owners={node:owner for node,owner in owners.items() if owner!=n};blocked=set(owners)
                    seeds,_=retained_branches(graph,[byname[n]],oldroutes,[newby[n]],[newby[n]],blocked)
                    weighted=dict(newby[n],criticality=1.)
                    rr=route(graph,[weighted],max_iterations=2,timeout=max(.01,min(10.,deadline-time.monotonic()-10)),max_expansions=40000,blocked_nodes=blocked,initial_trees=seeds)
                    if rr['status']!='routed':raise ValueError('Incident input could not reconnect with frozen neighbors')
                    changed[n]=rr['nets'][n];measured[n]=measure(newby[n],changed[n]);owners.update({node:n for node in changed[n]['nodes']})
                output_net=dict(newby[output],criticality=1.);variants=[]
                try:
                    vv,source_audit=reconnect_source(graph,byname[output],oldroutes['nets'][output],output_net,owners,min(deadline-10,time.monotonic()+12),max_depth=4,seconds_per_depth=3)
                    variants.extend((v['record'],dict(mode='source_reconnect',depth=v['depth'])) for v in vv)
                except (ValueError,TimeoutError) as e:source_audit=dict(error=str(e))
                rr=route(graph,[output_net],max_iterations=2,timeout=max(.01,min(10.,deadline-time.monotonic()-10)),max_expansions=60000,blocked_nodes=set(owners)|{byname[output]['source']})
                if rr['status']=='routed':variants.append((rr['nets'][output],dict(mode='whole_output_tree')))
                if not variants:raise ValueError('No output-tree reconnection')
                best=None;rejected=[]
                for record,method in variants:
                    combined=dict(oldroutes,nets={**oldroutes['nets'],**changed,output:record});validate(graph,newnets,combined)
                    om=measure(output_net,record);inputs=[];collateral={}
                    for p in input_pins:
                        before=list(delays(oldwire,p['net'])[p['old_node']]);after=list(measured[p['net']][p['new_node']])
                        oldarc=library.lut_input(p['index'],oldsite['slot'],1)[2:]+library.lut_input(p['index'],oldsite['slot'],2)[:2]
                        newarc=library.lut_input(p['index'],slot,1)[2:]+library.lut_input(p['index'],slot,2)[:2]
                        inputs.append(dict(p,before=[x+y for x,y in zip(before,oldarc)],after=[x+y for x,y in zip(after,newarc)]))
                    for n in affected-{output}:
                        moved={p['old_node'] for p in input_pins if p['net']==n};old=delays(oldwire,n)
                        collateral[n]=[dict(sink=s,before=v,after=measured[n][s]) for s,v in old.items() if s not in moved]
                    outputs=[dict(sink=s,before=v,after=om[s]) for s,v in delays(oldwire,output).items()]
                    guard=coupled_budget(inputs,outputs,collateral,budgets)
                    # Only credit the worst historical input/output combinations.
                    worst_in=min(budgets.get(p['old_node'],{}).get('original_setup_slack_ns',float('inf')) for p in inputs)
                    worst_out=min(budgets.get(p['sink'],{}).get('original_setup_slack_ns',float('inf')) for p in outputs)
                    ip={p['pin'] for p in inputs if budgets.get(p['old_node'],{}).get('original_setup_slack_ns',float('inf'))<=worst_in+.001}
                    op={p['sink'] for p in outputs if budgets.get(p['sink'],{}).get('original_setup_slack_ns',float('inf'))<=worst_out+.001}
                    gains=[-p['loss_ns']['setup'] for p in guard['coupled_paths'] if p['input_pin'] in ip and p['sink'] in op]
                    gain=min(gains) if gains else -float('inf')
                    if guard['accepted'] and gain>.01:
                        item=dict(info,critical_gain_lower_bound_ns=gain,affected_nets=sorted(affected),method=method,budget=guard)
                        if best is None or gain>best[0]:best=(gain,layout,{**changed,output:record},item)
                    else:rejected.append(dict(method=method,gain_ns=gain,normalized_loss_totals=guard['normalized_loss_totals'],unknown_losses=guard['unknown_or_zero_budget_losses'],budget=guard))
                trials.append(dict(info,source_search=source_audit,rejected=rejected,accepted_candidate=best is not None))
                if best:candidates.append(best)
            except (ValueError,TimeoutError) as e:trials.append(dict(info,error=str(e)))
            (a.out/'trials.json').write_text(json.dumps(trials,indent=2))
    if not candidates:
        (a.out/'manifest.json').write_text(json.dumps(dict(status='no_admissible_move',trial_count=len(trials),wall_seconds=time.monotonic()-start,pin_feedback=pin_info,neighbor_enumeration=enumeration,
            source_routes_sha256=sha(a.routes/'routes.json'),source_sta_sha256=sha(a.sta/'probe.tr'),vendor_pnr_executed=False,open_chip_database_used=False,timing_signoff=False),indent=2))
        print('No measured move satisfies the coupled budgets',flush=True);return
    selected=[];layout=copy.deepcopy(places);routes=copy.deepcopy(oldroutes);used=set();used_luts=set();totals={'setup':0.,'hold':0.};joint=None;dependencies=WireDependency(design)
    for gain,proposal,records,info in sorted(candidates,key=lambda x:(x[3]['worst_path_slack_ns'],-x[0],x[3]['lut'],x[3]['new_site'])):
        if info['lut'] in used_luts or used&set(records):continue
        combined=combined_budget(selected+[info],dependencies)
        if not combined['accepted']:continue
        np=copy.deepcopy(layout);np['clusters'][info['lut']]=proposal['clusters'][info['lut']];np['mixed_carry_lut_calibration']=proposal['mixed_carry_lut_calibration']
        nr=dict(routes,nets={**routes['nets'],**records})
        try:
            carry_validate(design,np,graph.device);ns,_,_=binding(np);validate(graph,ns,nr)
        except ValueError:continue
        layout,routes=np,nr;used.update(records);used_luts.add(info['lut']);totals=combined['normalized_loss_totals'];joint=combined;selected.append(info)
        if len(selected)>=a.batch_count:break
    if not selected:raise ValueError('No jointly legal measured move batch')
    nets,allnets,static=binding(layout);validate(graph,nets,routes);layout.update(timing_signoff=False,physical_legality_complete=False)
    placement=a.out/'placement';placement.mkdir();shutil.copyfile(a.placement/'packed.json',placement/'packed.json');(placement/'placement.json').write_text(json.dumps(layout,indent=2))
    for file,value in [('routes.json',routes),('nets.json',nets),('all_nets.json',allnets),('static_connections.json',static),('selected.json',selected)]:
        (a.out/file).write_text(json.dumps(value,indent=2))
    shutil.copyfile(a.routes/'clock_tree.json',a.out/'clock_tree.json')
    if hard:shutil.copyfile(a.routes/'hard_endpoint_audit.json',a.out/'hard_endpoint_audit.json')
    pm=dict(status='placed',netlist_sha256=sha(placement/'packed.json'),placement_sha256=sha(placement/'placement.json'),source_netlist_sha256=sha(a.placement/'packed.json'),source_placement_sha256=sha(a.placement/'placement.json'),logical_netlist_byte_identical=True,clock_endpoints_unchanged=True)
    (placement/'manifest.json').write_text(json.dumps(pm,indent=2))
    result=dict(pm,status='routed',net_count=len(nets),vendor_pnr_executed=False,open_chip_database_used=False,pin_feedback=pin_info,source_routes_sha256=sha(a.routes/'routes.json'),source_sta_sha256=sha(a.sta/'probe.tr'),
        selected=selected,combined_normalized_loss=totals,combined_budget=joint,trial_count=len(trials),wall_seconds=time.monotonic()-start,timing_signoff=False,connectivity_audit=validate(graph,nets,routes),
        native_source_sha256=native.source_sha256,lut_cell_library_sha256=library.sha256,carry_calibration_sha256=calibration()['capture_sha256'],neighbor_enumeration=enumeration)
    if hard:result['hard_endpoint_audit_sha256']=sha(a.out/'hard_endpoint_audit.json')
    (a.out/'manifest.json').write_text(json.dumps(result,indent=2));print(json.dumps(dict(selected=[dict(lut=r['lut'],site=r['new_site'],gain_ns=r['critical_gain_lower_bound_ns']) for r in selected],wall_seconds=result['wall_seconds']),indent=2))


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for n in ('placement','routes','sta','wire','pin-feedback','out'):p.add_argument('--'+n,type=Path,required=True)
    p.add_argument('--count',type=int,default=8);p.add_argument('--batch-count',type=int,default=4);p.add_argument('--seconds',type=int,default=600);p.add_argument('--worker',action='store_true');p.add_argument('--native-neighbors',action='store_true');p.add_argument('--only-lut',help='Explicit diagnostic filter; not used by automatic selection');a=p.parse_args()
    if not 1<=a.count<=32 or not 1<=a.batch_count<=4 or not 60<=a.seconds<=1200:p.error('Invalid bounded carry search')
    for n in ('placement','routes','sta','wire','pin_feedback','out'):setattr(a,n,getattr(a,n).resolve())
    if a.worker:return worker(a)
    from workspace_budget import reserve
    reserve(ROOT,400_000_000);prepare(a.out);sources=a.out/'sources';sources.mkdir()
    for n in ('ownpr_measured_carry_pack.py','ownpr_lut_move_budget.py','ownpr_pack_carry_neighbors.py','ownpr_source_reconnect.py','ownpr_incremental_routes.py','ownpr_router.py','ownpr_cell_timing.py','ownpr_pin_feedback.py','ownpr_route_design.py','ownpr_expand_routes.py','ownpr_timing_graph.py','ownpr_carry_lut_sharing.py','ownpr_carry_macros.py','native_device_session.py','ownpr_wire_budget.py'):
        shutil.copyfile(ROOT/'scripts'/n,sources/n)
    python=ROOT/'binary_audit/flow/runtime/bin/python.exe';cmd=[str(python),str(Path(__file__).resolve()),'--worker','--count',str(a.count),'--batch-count',str(a.batch_count),'--seconds',str(a.seconds)]
    if a.native_neighbors:cmd+=['--native-neighbors']
    if a.only_lut:cmd+=['--only-lut',a.only_lut]
    for n in ('placement','routes','sta','wire','pin_feedback','out'):cmd+=['--'+n.replace('_','-'),str(getattr(a,n))]
    start=time.monotonic()
    expired=False;rc=None
    with (a.out/'worker.log').open('w') as log:
        try:rc=subprocess.run(cmd,cwd=ROOT,env=dict(os.environ,PYTHONHOME=sys.base_prefix),stdout=log,stderr=subprocess.STDOUT,timeout=a.seconds+45,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0)).returncode
        except subprocess.TimeoutExpired:expired=True
    meta=load_json(a.out/'manifest.json') if (a.out/'manifest.json').exists() else {}
    meta.update(status=meta.get('status','failed') if rc==0 else 'timeout' if expired else 'failed',returncode=rc,total_wall_seconds=time.monotonic()-start,source_sha256={f.name:sha(f) for f in sources.iterdir()})
    (a.out/'manifest.json').write_text(json.dumps(meta,indent=2));print(json.dumps({k:meta.get(k) for k in ('status','total_wall_seconds','trial_count')},indent=2))
    if rc!=0:raise SystemExit(1)


if __name__=='__main__':main()
