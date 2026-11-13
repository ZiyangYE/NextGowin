"""Restore better historical trees and negotiate neighbors under loaded budgets.

Only identical source/sink contracts are reusable. Setup and hold pin budgets
come from the current audited STA, never from the historical implementation.
"""
import argparse,copy,json,os,shutil,subprocess,sys,time
from pathlib import Path
from ownpr_device import ROOT
from ownpr_image_storage import load_json,sha,read_text
from ownpr_wire_budget import ledger,delays,WireDependency

def restoration_sizes(count):
    if count<0:raise ValueError('Negative restoration count')
    return sorted({count,max(1,count//2),1},reverse=True) if count else []

def checked_wire(folder,routes):
    m=load_json(folder/'manifest.json');d=load_json(folder/'wire_delays.json')
    if m['status']!='measured' or m['vendor_pnr_executed'] or sha(folder/'wire_delays.json')!=m['wire_delays_sha256']:raise ValueError('Unverified wire measurement')
    if d['source_routes_sha256']!=sha(routes/'routes.json') or d['source_nets_sha256']!=sha(routes/'nets.json'):raise ValueError('Wire measurements refer to different routes')
    return d

def worker(a):
    from native_device_session import NativeDeviceSession
    from ownpr_device import Device
    from ownpr_timing_graph import TimingRoutingGraph
    from ownpr_wide_ff_packing import configure_graph
    from ownpr_expand_routes import expand
    from ownpr_critical_tree_rebuild import verify_feedback
    from ownpr_pin_feedback import load,physical_budgets,routing_priorities,criticalities
    from ownpr_incremental_routes import signature
    from ownpr_router import route,validate,RoutingTimeout
    native=NativeDeviceSession(a.out);start=time.monotonic();deadline=start+a.seconds
    design=load_json(a.placement/'packed.json');placed=load_json(a.placement/'placement.json');meta,sm,_=verify_feedback(a.placement,a.routes,a.sta)
    report=load_json(a.history);rows=report['designs']
    if len(rows)!=1:raise ValueError('Historical report must identify one design')
    h=rows[0]
    for name,digest in h['selected_source_sha256'].items():
        if sha(ROOT/name)!=digest:raise ValueError('Historical checkpoint changed')
    hs={k:ROOT/v for k,v in h['selected'].items()};_,hm,_=verify_feedback(hs['placement'],hs['routes'],hs['sta'])
    if sm['cst_sha256']!=hm['cst_sha256']:raise ValueError('Historical package-pin contract differs')
    current=load_json(a.routes/'routes.json');history=load_json(hs['routes']/'routes.json');nets=load_json(a.routes/'nets.json');byname={n['name']:n for n in nets};hn={n['name']:n for n in load_json(hs['routes']/'nets.json')}
    current_wire=checked_wire(a.wire,a.routes);history_wire=checked_wire(hs['wire'],hs['routes'])
    graph=TimingRoutingGraph(Device(),ROOT/'results/native_rrg_001',native);configure_graph(graph,design,placed)
    graph.heuristic_scale=.03;graph.root_arrival_weight=1.;graph.guided_corridor=True;graph.guide_edge_delay=.05
    logical,pin_audit=load(a.pin_feedback,a.sta);budgets=physical_budgets(graph,load_json(a.sta/'sta_endpoint_audit.json'),logical);dependencies=WireDependency(design)
    def budget_for(changes):return ledger(changes,budgets,dependencies.relations(changes))
    search_nets,priority_audit=routing_priorities(nets,budgets);search_byname={n['name']:n for n in search_nets};weights=criticalities(budgets)
    reserved=set();clock=load_json(a.routes/'clock_tree.json')
    for row in clock['parents']:
        r,c,i=row['node']
        if min(r,c)>=1 and i in graph.device.names:reserved.add(graph.node(r-1,c-1,graph.device.names[i]))
    from ownpr_ioreg_macros import reserved_points
    reserved.update(graph.node(r-1,c-1,graph.device.names[i]) for r,c,i in reserved_points(design,placed))
    endpoints={node:n['name'] for n in nets for node in [*n.get('sources',[n['source']]),*n['sinks']]}
    cache={}
    def measure(net,record):
        key=(net['name'],tuple(sorted(map(tuple,record['pips']))))
        if key in cache:return cache[key]
        data=expand(graph,[net],dict(nets={net['name']:record}),native.fanouts)['nets'][net['name']]
        parents={tuple(p['node']):tuple(p['parent']) if p['parent'] else None for p in data['parents']};values={}
        with native.parent_tree(parents):
            costs={p:(native.delay(p,1),native.delay(p,2)) for p in parents}
            for sink in data['sinks']:
                chain=list(map(tuple,sink['path_sink_to_source']));values[sink['resource']]=[sum(costs[p][s][i] for p in chain) for s,i in ((0,2),(0,3),(1,0),(1,1))]
        cache[key]=values;return values
    options=[];rejected=[]
    for net in nets:
        name=net['name']
        if name not in hn or net.get('clock') or 'constant_value' in net or signature(net)!=signature(hn[name]):continue
        before=delays(current_wire,name);old=delays(history_wire,name)
        score=max((min(before[s][i]-old[s][i] for i in (0,1))*weights.get(s,0.) for s in before),default=0.)
        if score<.2:continue
        row=history['nets'][name];nodes=set(row['nodes'])
        if nodes&reserved or any(n in endpoints and endpoints[n]!=name for n in nodes):
            rejected.append(dict(net=name,reason='historical tree overlaps a current reserved resource or foreign endpoint'));continue
        try:validate(graph,[net],dict(nets={name:row}))
        except ValueError as e:rejected.append(dict(net=name,reason=str(e)));continue
        options.append(dict(net=name,priority=score))
    options.sort(key=lambda r:(-r['priority'],r['net']));chosen=[];target_changes={}
    for option in options[:24]:
        if time.monotonic()>deadline-60:break
        name=option['net'];net=byname[name];before=measure(net,current['nets'][name]);after=measure(net,history['nets'][name])
        # This cross-check makes the stored ranking data reproducibly advisory.
        stored=delays(current_wire,name)
        if any(abs(before[s][i]-stored[s][i])>.0001 for s in before for i in range(4)):raise ValueError('Current loaded tree measurement changed')
        candidate={**target_changes,name:(before,after)};budget=budget_for(candidate)
        if not budget['accepted']:
            rejected.append(dict(net=name,reason='restored trees exceed shared pin budgets',budget=budget));continue
        chosen.append(option);target_changes=candidate
        if len(chosen)>=a.count:break
    (a.out/'plan.json').write_text(json.dumps(dict(options=options,chosen=chosen,rejected=rejected,pin_audit=pin_audit,priority_audit=priority_audit),indent=2))
    trials=[];selected=None
    for number in restoration_sizes(len(chosen)):
        if not number or time.monotonic()>deadline-45:continue
        graph.root_arrival_weight=1.
        targets={r['net'] for r in chosen[:number]};fixed={n:history['nets'][n] for n in targets};blocked=reserved|{v for r in fixed.values() for v in r['nodes']}
        others=[n for n in search_nets if n['name'] not in targets]
        cp=dict(status='incomplete_route_checkpoint',iterations=[],nets={n['name']:dict(current['nets'][n['name']],retained_sinks=n['sinks']) for n in others})
        trial=dict(targets=sorted(targets),rounds=[]);trials.append(trial)
        for attempt in range(2):
            remaining=deadline-time.monotonic()
            if remaining<30:break
            events=[]
            def progress(row):
                if row['event'] in ('iteration_done','negotiation_resume'):events.append(row);print(json.dumps(row),flush=True)
            try:result=route(graph,others,timeout=min(220,remaining-10),max_iterations=16,max_expansions=100000,blocked_nodes=blocked,warm_checkpoint=cp,progress=progress)
            except RoutingTimeout as e:
                trial['rounds'].append(dict(status='timeout',events=events));cp=e.checkpoint;continue
            if result['status']!='routed':trial['rounds'].append(dict(status=result['status'],events=events));break
            combined=dict(result,nets={**result['nets'],**fixed});check=validate(graph,nets,combined)
            changed={n for n in byname if set(map(tuple,current['nets'][n]['pips']))!=set(map(tuple,combined['nets'][n]['pips']))}
            changes={n:(measure(byname[n],current['nets'][n]),measure(byname[n],combined['nets'][n])) for n in changed}
            budget=budget_for(changes);row=dict(status='routed',changed_nets=sorted(changed),budget=budget,connectivity=check,events=events);trial['rounds'].append(row)
            (a.out/'trials.json').write_text(json.dumps(trials,indent=2))
            if budget['accepted']:
                selected=combined;trial['selected']=True;break
            # Arrival-oriented search cannot repair a minimum-delay deficit.
            # Reduce the restoration set instead of blindly shortening wires.
            hold=budget['normalized_loss_totals']['hold']
            if hold is None or hold>1.:break
            # Retry with stronger estimated arrival preference at violating
            # sinks. Acceptance still uses the complete loaded-tree ledger.
            offenders={r['net'] for r in budget['nets'] if r['charges']['setup'] is None or r['charges']['setup']>0}-targets
            if not offenders:break
            cp=dict(status='incomplete_route_checkpoint',iterations=result['iterations'],nets={n['name']:dict(result['nets'][n['name']],retained_sinks=n['sinks']) for n in others if n['name'] not in offenders})
            others=[dict(n,criticality=1.,sink_criticalities={s:1. for s in n['sinks']}) if n['name'] in offenders else n for n in others];graph.root_arrival_weight=4.
        (a.out/'trials.json').write_text(json.dumps(trials,indent=2))
        if selected:break
    if selected is None:selected=copy.deepcopy(current)
    check=validate(graph,nets,selected)
    for filename in ('nets.json','all_nets.json','clock_tree.json','static_connections.json','hard_endpoint_audit.json'):
        if (a.routes/filename).exists():shutil.copyfile(a.routes/filename,a.out/filename)
    (a.out/'routes.json').write_text(json.dumps(selected));result=dict(meta,status='routed',connectivity_audit=check,iterations=selected.get('iterations',[]),
        budgeted_restoration_applied=any(t.get('selected') for t in trials),budgeted_restore_trials=trials,
        source_routes_sha256=sha(a.routes/'routes.json'),source_sta_sha256=sha(a.sta/'probe.tr'),historical_report_sha256=sha(a.history),pin_feedback=pin_audit,
        netlist_sha256=sha(a.placement/'packed.json'),placement_sha256=sha(a.placement/'placement.json'),wall_seconds=time.monotonic()-start,
        full_design_sta_required=True,timing_signoff=False,vendor_pnr_executed=False)
    (a.out/'manifest.json').write_text(json.dumps(result,indent=2));print('RESTORATION_APPLIED',result['budgeted_restoration_applied'],flush=True)

def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('placement','routes','sta','wire','pin-feedback','history','out'):p.add_argument('--'+name,type=Path,required=True)
    p.add_argument('--count',type=int,default=4);p.add_argument('--seconds',type=int,default=600);p.add_argument('--worker',action='store_true');a=p.parse_args()
    if not 1<=a.count<=8 or not 60<=a.seconds<=1800:p.error('Invalid bounded repair limits')
    for name in ('placement','routes','sta','wire','pin_feedback','history','out'):setattr(a,name,getattr(a,name).resolve())
    if a.worker:return worker(a)
    from workspace_budget import reserve
    from gowin_probe import prepare
    reserve(ROOT,200_000_000);prepare(a.out)
    for name in ('ownpr_budgeted_restore.py','ownpr_wire_budget.py','ownpr_pin_feedback.py','ownpr_router.py'):shutil.copyfile(ROOT/'scripts'/name,a.out/(name+'.txt'))
    python=ROOT/'binary_audit/flow/runtime/bin/python.exe';command=[str(python),str(Path(__file__).resolve()),'--worker','--seconds',str(a.seconds),'--count',str(a.count)]
    for name in ('placement','routes','sta','wire','pin_feedback','history','out'):command+=['--'+name.replace('_','-'),str(getattr(a,name))]
    start=time.monotonic()
    with (a.out/'worker.log').open('w') as log:
        try:rc=subprocess.run(command,cwd=ROOT,env=dict(os.environ,PYTHONHOME=sys.base_prefix),stdout=log,stderr=subprocess.STDOUT,timeout=a.seconds+90,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0)).returncode
        except subprocess.TimeoutExpired:rc=-1
    if rc:
        (a.out/'failure.json').write_text(json.dumps(dict(status='failed',returncode=rc,wall_seconds=time.monotonic()-start),indent=2));raise SystemExit('Budgeted restore failed; see worker.log')
    m=load_json(a.out/'manifest.json');m['total_wall_seconds']=time.monotonic()-start;m['producer_source_sha256']={f.name:sha(f) for f in a.out.glob('*.py.txt')};(a.out/'manifest.json').write_text(json.dumps(m,indent=2));print(json.dumps(dict(applied=m['budgeted_restoration_applied'],seconds=m['total_wall_seconds']),indent=2))

if __name__=='__main__':main()
