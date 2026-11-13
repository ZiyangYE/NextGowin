"""Joint state-alias receiver-subtree transfer and measured route reconnection.

One persistent native data session evaluates complete donor/recipient trees.
No state, cell positions, package pins or clocks move. Only candidates passing
global setup/hold feedback are committed; final native STA remains mandatory.
"""
import argparse,copy,json,os,shutil,subprocess,sys,time
from collections import defaultdict
from pathlib import Path
from gowin_probe import ROOT,prepare
from ownpr_image_storage import load_json,sha


def worker(a):
    from native_device_session import NativeDeviceSession
    from ownpr_device import Device
    from ownpr_timing_graph import TimingRoutingGraph
    from ownpr_wide_ff_packing import configure_graph
    from ownpr_clock_graph import ClockGraph
    from ownpr_clock_partition import partition
    from ownpr_route_design import bind
    from ownpr_router import validate
    from ownpr_incremental_routes import signature
    from ownpr_source_reconnect import reconnect_source
    from ownpr_route_branch import branches,prune
    from ownpr_expand_routes import expand
    from ownpr_budgeted_restore import checked_wire
    from ownpr_critical_tree_rebuild import verify_feedback
    from ownpr_full_fanout_alias import propose,certify
    from ownpr_alias_feedback import AliasFeedback,admissible
    from audit_native_wire_delays import f32
    start=time.monotonic();deadline=start+a.seconds;native=NativeDeviceSession(a.out)
    verify_feedback(a.placement,a.routes,a.sta);mm=load_json(a.model/'manifest.json')
    for p,h in mm['source_sha256'].items():
        if sha(Path(p))!=h:raise ValueError('Changed timing model input')
    if mm['status']!='validated_setup_hold_feedback' or sha(a.model/'model.json')!=mm['model_sha256']:raise ValueError('Unvalidated timing feedback')
    original=load_json(a.placement/'packed.json');layout=load_json(a.placement/'placement.json');pm=load_json(a.placement/'manifest.json');model=load_json(a.model/'model.json')
    if mm['source_sha256'].get(str(a.placement/'packed.json'))!=sha(a.placement/'packed.json'):raise ValueError('Model belongs to another source')
    _,families,aliases=propose(original,layout,model,pm.get('register_replicas',{}),32,4)
    certify(original,original,aliases,a.out/'before_invariant')
    graph=TimingRoutingGraph(Device(),ROOT/'results/native_rrg_001',native);configure_graph(graph,original,layout)
    graph.heuristic_scale=.03;graph.root_arrival_weight=1.;graph.guided_corridor=True;graph.guide_edge_delay=.05
    oldnets=load_json(a.routes/'nets.json');routes=load_json(a.routes/'routes.json');checked_wire(a.wire,a.routes)
    hard=load_json(a.routes/'hard_endpoint_audit.json') if layout.get('hard_clusters') else None
    if hard and sha(a.routes/'hard_endpoint_audit.json')!=load_json(a.routes/'manifest.json')['hard_endpoint_audit_sha256']:raise ValueError('Changed hard endpoint evidence')
    clock=load_json(a.routes/'clock_tree.json');clock_model=ClockGraph.for_tree(clock)
    reserved={graph.node(r-1,c-1,graph.device.names[i]) for row in clock['parents'] for r,c,i in [row['node']] if min(r,c)>=1 and i in graph.device.names}
    from ownpr_ioreg_macros import reserved_points
    reserved.update(graph.node(r-1,c-1,graph.device.names[i]) for r,c,i in reserved_points(original,layout))
    hardnames=set(layout.get('hard_clusters',{}))
    def binding(design):
        if any(design['modules']['top']['cells'][n]!=original['modules']['top']['cells'][n] for n in hardnames):raise ValueError('Reused hard evidence requires identical complete hard cells')
        sinks=[];allnets,static=bind(design,layout,graph,hard_bindings=hard,logical_sinks=sinks)
        return partition(graph,allnets,clock,clock_model)[0],allnets,static,sinks
    design=original;nets,allnets,static,logical=binding(design);byname={n['name']:n for n in nets}
    if {n['name']:signature(n) for n in oldnets}!={n['name']:signature(n) for n in nets}:raise ValueError('Baseline binding differs')
    validate(graph,nets,routes)
    feedback=AliasFeedback(model,load_json(a.sta/'sta_endpoint_audit.json'),graph,original,aliases);graphs={'setup':model['late'],'hold':model['early']};metrics=feedback.metrics(graphs);initial_metrics={n:{k:r[k] for k in ('wns','tns','violations')} for n,r in metrics.items()}
    # Reproduce native wire calibration before measuring any proposal.
    reference=load_json(ROOT/'results/native_wire_delays_009/native_wire_delays.json');parents={tuple(r['node']):tuple(r['parent']) if r['parent'] else None for r in reference['nodes']}
    with native.parent_tree(parents):
        for row in reference['nodes']:
            if [list(native.delay(tuple(row['node']),s)) for s in range(3)]!=row['values_by_selector']:raise ValueError('Wire timing reference mismatch')
    def measure(net,record):
        data=expand(graph,[net],dict(nets={net['name']:record}),native.fanouts)['nets'][net['name']]
        parents={tuple(p['node']):tuple(p['parent']) if p['parent'] else None for p in data['parents']}
        with native.parent_tree(parents):values={p:(native.delay(p,1),native.delay(p,2)) for p in parents}
        measured={}
        for s in data['sinks']:
            sums=[]
            for selector,index in ((0,2),(0,3),(1,0),(1,1)):
                v=0.
                for p in s['path_sink_to_source']:v=f32(v+values[tuple(p)][selector][index])
                sums.append(v)
            measured[s['resource']]=sums
        return measured,parents,values
    cache={};trials=[];selected=[];seen=set();hold_reference=metrics
    for iteration in range(a.rounds):
        if time.monotonic()+45>deadline or len(trials)>=a.trials:break
        byresource=defaultdict(list)
        for s in logical:byresource[s['net'],s['node']].append(s)
        candidates=[];cells=design['modules']['top']['cells']
        for family in families:
            members=family['members'];eligible={(s['instance'],s['pin']) for s in family['sinks']}
            for donor in members:
                donor_name='net_'+str(cells[donor]['connections']['Q'][0]);net=byname.get(donor_name)
                if not net or len(net['sinks'])<4:continue
                if donor_name not in cache:cache[donor_name]=measure(net,routes['nets'][donor_name])
                _,parentmap,values=cache[donor_name]
                for branch in branches(graph,net,routes['nets'][donor_name],2,32,12):
                    pins=[s for resource in branch['sinks'] for s in byresource.get((donor_name,resource),[])]
                    if len({s['node'] for s in pins})!=len(branch['sinks']) or any((s['instance'],s['pin']) not in eligible for s in pins):continue
                    p=branch['incoming_pip'];point=(p[0]+1,p[1]+1,graph.device.ids[p[3]]);cost=[0.,0.];visited=set()
                    while point is not None:
                        if point in visited or point not in parentmap:raise ValueError('Invalid physical prefix')
                        visited.add(point)
                        for tr in (0,1):cost[tr]+=values[point][0][2+tr]
                        point=parentmap[point]
                    slacks=[min(metrics['setup']['slack'][s['instance']+'/'+s['pin']]) for s in pins];hot=min(slacks);prefix=max(cost)
                    for recipient in members:
                        key=(donor,recipient,tuple(branch['sinks']),branch['anchor'])
                        rn='net_'+str(cells[recipient]['connections']['Q'][0])
                        if recipient==donor or rn not in byname or key in seen:continue
                        y,x=graph[branch['anchor']];ry,rx=graph[byname[rn]['source']];distance=abs(y-ry)+abs(x-rx)
                        if distance>24:continue
                        # Hot subtrees and cold load-relief subtrees both compete.
                        potential=sum(min(max(.05,-s),prefix) for s in slacks)
                        score=(min(metrics['setup']['slack'][donor+'/Q']),-potential,distance,hot,donor,recipient,branch['anchor'])
                        candidates.append((score,dict(branch,family=family['representative'],donor=donor,recipient=recipient,donor_net=donor_name,recipient_net=rn,pins=pins,prefix_ns=prefix,worst_receiver_slack_ns=hot,receiver_distance_tiles=distance,key=key)))
        # Interleave branches that directly carry violations and cold branches
        # whose removal can relieve the shared driver. Ranking never adopts.
        ranked=sorted(candidates,key=lambda x:x[0]);queues={};ordered=[]
        for _,row in ranked:queues.setdefault(row['family'],[])
        for family in queues:
            hot=[c for c in ranked if c[1]['family']==family and c[1]['worst_receiver_slack_ns']<0];cold=[c for c in ranked if c[1]['family']==family and c[1]['worst_receiver_slack_ns']>=0]
            while hot or cold:
                queues[family].extend(hot[:2]);hot=hot[2:];queues[family].extend(cold[:1]);cold=cold[1:]
        # Prevent one large near-tied source from consuming the entire beam.
        # Coupled critical families each receive measured candidates per round.
        while any(queues.values()):
            for family in queues:
                if queues[family]:ordered.append(queues[family].pop(0))
        best=None
        for _,proposal in ordered[:a.per_round]:
            if len(trials)>=a.trials or time.monotonic()+25>deadline:break
            seen.add(proposal['key']);dn,rn=proposal['donor_net'],proposal['recipient_net'];info={k:v for k,v in proposal.items() if k not in ('key','pins')};info['iteration']=iteration
            try:
                new=copy.deepcopy(design);newbit=cells[proposal['recipient']]['connections']['Q']
                for s in proposal['pins']:new['modules']['top']['cells'][s['instance']]['connections'][s['pin']]=list(newbit)
                ns,na,nstatic,nlogical=binding(new);nb={n['name']:n for n in ns}
                if set(byname)!=set(nb):raise ValueError('Subtree assignment changed the physical net set')
                changed={n for n in byname if signature(byname[n])!=signature(nb[n])}
                if changed!={dn,rn}:raise ValueError('Subtree assignment changed an unexpected physical net')
                remaining=set(byname[dn]['sinks'])-set(proposal['sinks']);donor_record=prune(graph,byname[dn],routes['nets'][dn],remaining)
                owners={node:n for n,r in routes['nets'].items() if n not in (dn,rn) for node in r['nodes']};owners.update({node:dn for node in donor_record['nodes']});owners.update({node:'__reserved__' for node in reserved})
                synthetic=dict(byname[dn],name=rn);depth=proposal['depth'];variants,audit=reconnect_source(graph,synthetic,routes['nets'][dn],dict(nb[rn],criticality=1.),owners,min(deadline-10,time.monotonic()+10),max_depth=depth,seconds_per_depth=4,min_depth=depth)
                info['reconnection']=audit
                if not variants:raise ValueError('No legal connector to the exact retained receiver subtree')
                dm=measure(nb[dn],donor_record)
                for variant in variants:
                    nr=dict(routes,nets={**routes['nets'],dn:donor_record,rn:variant['record']});validate(graph,ns,nr)
                    rm=measure(nb[rn],variant['record']);ng=feedback.update(graphs,new,{dn:dm[0],rn:rm[0]});nm=feedback.metrics(ng)
                    row=dict(info,setup={k:nm['setup'][k] for k in ('wns','tns','violations')},hold={k:nm['hold'][k] for k in ('wns','tns','violations')},admissible=admissible(nm,metrics,hold_reference),retained_pips=variant['retained_pips'],connector_pips=variant['connector_pips'])
                    trials.append(row)
                    if row['admissible']:
                        key=(-nm['setup']['wns'],-nm['setup']['tns'],len(variant['record']['pips']))
                        if best is None or key<best[0]:best=(key,new,ns,na,nstatic,nlogical,nr,ng,nm,{dn:dm,rn:rm},row)
            except (ValueError,TimeoutError) as e:trials.append(dict(info,error=str(e),admissible=False))
            (a.out/'trials.json').write_text(json.dumps(trials,indent=2));print('BRANCH_TRIAL',len(trials),trials[-1].get('setup'),trials[-1].get('error',''),flush=True)
        if best is None:break
        _,design,nets,allnets,static,logical,routes,graphs,metrics,updates,row=best;cache.update(updates);byname={n['name']:n for n in nets};selected.append(row)
        (a.out/'selected.json').write_text(json.dumps(selected,indent=2));print('BRANCH_COMMITTED',len(selected),row['setup'],flush=True)
    common=dict(trial_count=len(trials),accepted_transactions=len(selected),wall_seconds=time.monotonic()-start,baseline_metrics=initial_metrics,current_metrics={n:{k:r[k] for k in ('wns','tns','violations')} for n,r in metrics.items()},vendor_pnr_executed=False,open_chip_database_used=False,timing_signoff=False)
    if not selected:
        (a.out/'manifest.json').write_text(json.dumps(dict(common,status='no_admissible_transfer'),indent=2));return
    proof=certify(original,design,aliases,a.out/'final_invariant');placement=a.out/'placement';placement.mkdir();(placement/'packed.json').write_text(json.dumps(design,indent=2));shutil.copyfile(a.placement/'placement.json',placement/'placement.json')
    pm=dict(status='placed',netlist_sha256=sha(placement/'packed.json'),placement_sha256=sha(placement/'placement.json'),source_netlist_sha256=sha(a.placement/'packed.json'),source_placement_sha256=sha(a.placement/'placement.json'),source_model_sha256=sha(a.model/'model.json'),register_aliases=aliases,proof=proof,clock_endpoints_unchanged=True,layout_byte_identical=True,lut_delta=0,register_delta=0,timing_signoff=False)
    (placement/'manifest.json').write_text(json.dumps(pm,indent=2))
    for name,data in [('routes.json',routes),('nets.json',nets),('all_nets.json',allnets),('static_connections.json',static)]: (a.out/name).write_text(json.dumps(data,indent=2))
    shutil.copyfile(a.routes/'clock_tree.json',a.out/'clock_tree.json')
    if hard:shutil.copyfile(a.routes/'hard_endpoint_audit.json',a.out/'hard_endpoint_audit.json')
    result=dict(pm,**{k:v for k,v in common.items() if k not in pm},status='routed',net_count=len(nets),connectivity_audit=validate(graph,nets,routes),source_routes_sha256=sha(a.routes/'routes.json'),native_source_sha256=native.source_sha256,reused_hard_endpoints_scope='Complete hard cell dictionaries and physical sites unchanged; final native import/readback still required')
    if hard:result['hard_endpoint_audit_sha256']=sha(a.out/'hard_endpoint_audit.json')
    (a.out/'manifest.json').write_text(json.dumps(result,indent=2))


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for n in ('placement','routes','wire','sta','model','out'):p.add_argument('--'+n,type=Path,required=True)
    p.add_argument('--seconds',type=int,default=900);p.add_argument('--trials',type=int,default=36);p.add_argument('--rounds',type=int,default=3);p.add_argument('--per-round',type=int,default=12);p.add_argument('--worker',action='store_true');a=p.parse_args()
    if not 60<=a.seconds<=1800 or not 1<=a.trials<=64 or not 1<=a.rounds<=4 or not 1<=a.per_round<=24:p.error('Invalid bounded transaction search')
    for n in ('placement','routes','wire','sta','model','out'):setattr(a,n,getattr(a,n).resolve())
    if a.worker:return worker(a)
    from workspace_budget import reserve
    reserve(ROOT,400_000_000);prepare(a.out);sources=a.out/'sources';sources.mkdir()
    for n in ('ownpr_measured_branch_transfer.py','ownpr_route_branch.py','ownpr_alias_feedback.py','ownpr_source_reconnect.py','ownpr_bidir_timing.py','ownpr_full_fanout_alias.py','ownpr_router.py','ownpr_expand_routes.py'):shutil.copyfile(ROOT/'scripts'/n,sources/n)
    cmd=[str(ROOT/'binary_audit/flow/runtime/bin/python.exe'),str(Path(__file__).resolve()),'--worker','--seconds',str(a.seconds),'--trials',str(a.trials),'--rounds',str(a.rounds),'--per-round',str(a.per_round)]
    for n in ('placement','routes','wire','sta','model','out'):cmd+=['--'+n,str(getattr(a,n))]
    start=time.monotonic();code=None;expired=False
    with (a.out/'worker.log').open('w') as log:
        try:code=subprocess.run(cmd,cwd=ROOT,env=dict(os.environ,PYTHONHOME=sys.base_prefix),stdout=log,stderr=subprocess.STDOUT,timeout=a.seconds+45,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0)).returncode
        except subprocess.TimeoutExpired:expired=True
    m=load_json(a.out/'manifest.json') if (a.out/'manifest.json').exists() else {};m.update(status=m.get('status','failed') if code==0 else 'timeout' if expired else 'failed',returncode=code,total_wall_seconds=time.monotonic()-start,source_sha256={f.name:sha(f) for f in sources.iterdir()});(a.out/'manifest.json').write_text(json.dumps(m,indent=2));print(json.dumps({k:m.get(k) for k in ('status','trial_count','accepted_transactions','current_metrics','total_wall_seconds')},indent=2))
    if code:raise SystemExit(1)


if __name__=='__main__':main()
