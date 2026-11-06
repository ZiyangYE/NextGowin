"""Bounded critical-net tree rebuilding from audited, possibly passing STA.

No placement or logical changes. Each variant is measured on its complete
native parent tree; local eligibility is not full-design timing acceptance.
"""
import argparse,json,os,re,shutil,subprocess,sys,time
from pathlib import Path
from ownpr_device import ROOT
from ownpr_image_storage import load_json,read_text,sha
from workspace_budget import reserve


def endpoint_budgets(graph,design,captures,text,cone_budgets=False):
    from ownpr_ff_configuration import FF_CONTROLS
    from collections import defaultdict
    tables={}
    for index,kind in ((1,'Setup'),(2,'Hold')):
        section=re.search(r'^3\.1\.'+str(index)+' '+kind+r' Paths Table\n(.*?)(?=\n3\.)',text,re.M|re.S)
        if not section:raise ValueError('Missing complete timing endpoint table')
        table={}
        for slack,endpoint in re.findall(r'^\s*\d+\s+([-\d.]+)\s+\S+\s+(\S+)\s+',section[1],re.M):
            if endpoint in table:raise ValueError('Duplicate timing endpoint budget')
            table[endpoint]=float(slack)
        if not table:raise ValueError('Empty timing endpoint budgets')
        tables[kind]=table
    cells=design['modules']['top']['cells'];by_resource=defaultdict(list)
    for row in captures:
        if row['direction']!='In' or len(row['nodes'])!=1:continue
        r,c,i=row['nodes'][0]['node']
        if min(r,c)<1 or i not in graph.device.names:continue
        node=graph.node(r-1,c-1,graph.device.names[i]);typ=cells[row['instance']]['type'];pin=row['pin'];endpoint=None
        if typ in FF_CONTROLS and pin in ('D','CE'):endpoint=row['native_name']+'/'+pin
        elif typ=='OBUF' and pin=='I':endpoint=row['native_name']+'/O'
        by_resource[node].append(endpoint)
    fallback=dict(setup=max(0.,min(tables['Setup'].values())-.001),hold=max(0.,min(tables['Hold'].values())-.01),scope='global_margin_for_internal_sink')
    result={}
    for node,labels in by_resource.items():
        if all(label in tables['Setup'] and label in tables['Hold'] for label in labels):
            result[node]=dict(setup=max(0.,min(tables['Setup'][p] for p in labels)-.001),hold=max(0.,min(tables['Hold'][p] for p in labels)-.01),
                original_setup_slack_ns=min(tables['Setup'][p] for p in labels),endpoints=labels,scope='terminal_endpoint_slack')
    if cone_budgets:
        from ownpr_cone_budgets import physical_budgets
        inferred=physical_budgets(graph,design,captures,tables)
        result={**inferred,**result}
    return result,fallback


def eligible(before,after,critical_sinks,budgets=None):
    if set(before)!=set(after) or not set(critical_sinks)<=set(before):raise ValueError('Tree measurement sink set changed')
    # Preserve both slow transitions at other sinks and budget fast-corner
    # loss across the whole tree. Final setup/hold is always mandatory.
    setup_loss=max(after[s][i]-before[s][i] for s in before for i in (0,1))
    hold_loss=max(before[s][i]-after[s][i] for s in before for i in (2,3))
    gains={s:min(before[s][i]-after[s][i] for i in (0,1)) for s in critical_sinks}
    gain=max(gains.values())
    bounds=budgets or {s:dict(setup=.005,hold=.02) for s in before};violations=[];predicted=[]
    if set(bounds)!=set(before):raise ValueError('Incomplete sink timing budgets')
    for s in before:
        slow=max(after[s][i]-before[s][i] for i in (0,1));fast=max(before[s][i]-after[s][i] for i in (2,3));b=bounds[s]
        if slow>b['setup']+1e-9 or fast>b['hold']+1e-9:violations.append(dict(sink=s,setup_loss_ns=slow,hold_loss_ns=fast,budget=b))
        predicted.append(b.get('original_setup_slack_ns',b['setup']+.001)-slow)
    return not violations and gain>=.01,dict(max_setup_loss_ns=setup_loss,max_hold_loss_ns=hold_loss,critical_min_transition_gains_ns=gains,best_critical_gain_ns=gain,
        predicted_min_sink_setup_slack_ns=min(predicted),budget_violations=violations)


def exploratory(delta,limit):
    return bool(limit>0 and delta['best_critical_gain_ns']>=.01 and delta['predicted_min_sink_setup_slack_ns']>=-limit
        and all(v['hold_loss_ns']<=v['budget']['hold']+1e-9 for v in delta['budget_violations']))


def hold_exploratory(delta,limit):
    """Explicitly infeasible setup-recovery candidate, requiring hold repair."""
    return bool(limit>0 and delta['best_critical_gain_ns']>=1. and delta['predicted_min_sink_setup_slack_ns']>=0
        and all(v['setup_loss_ns']<=v['budget']['setup']+1e-9 and v['hold_loss_ns']-v['budget']['hold']<=limit for v in delta['budget_violations']))


def worst_slack_exploratory(delta,wns):
    """Infeasible proposal: bounded setup trade within the old worst slack.

Never a signoff waiver. Hold budgets stay intact and final whole-design STA
must show an actual improvement before any state can be retained.
"""
    return bool(wns<-.05 and delta['best_critical_gain_ns']>=.1 and delta['max_setup_loss_ns']<=.75
        and delta['predicted_min_sink_setup_slack_ns']>=wns+.05
        and all(v['hold_loss_ns']<=v['budget']['hold']+1e-9 for v in delta['budget_violations']))


def critical_labels(candidate,traces):
    """Optimize the worst timing path, not a longer wire on a relaxed path."""
    slacks={}
    for path in traces:
        for arc in path['ordinary_wires']:
            if arc['net']==candidate['net']:
                label=arc['endpoint'];slacks[label]=min(slacks.get(label,float('inf')),path['slack_ns'])
    if set(slacks)!=set(candidate['endpoints']):raise ValueError('Critical receiver ranking does not match path witnesses')
    worst=min(slacks.values())
    return worst,sorted(label for label,slack in slacks.items() if slack<=worst+1e-9)


def search_candidates(graph,net,current,critical,owners,deadline,mode):
    """Search either a terminal branch or the entire tree under the same cap."""
    if mode not in ('whole','branch','both'):raise ValueError('Unknown critical route search mode')
    candidates=[];search={}
    if mode in ('branch','both'):
        from ownpr_branch_reconnect import reconnect
        try:
            rows,stats=reconnect(graph,net,current,critical,owners,min(deadline,time.monotonic()+45),max_expansions=12000,max_candidates=64)
        except TimeoutError:
            rows=[];stats=dict(status='search_timeout',candidates=0)
        candidates.extend(rows);search['branch']=stats
    if mode in ('whole','both') and time.monotonic()<deadline:
        from ownpr_whole_net_eco import rebuild
        rows,stats=rebuild(graph,net,critical,owners,min(deadline,time.monotonic()+85),seconds_per_variant=25,root_weights=(0.,4.,8.))
        candidates.extend(rows);search['whole']=stats
    return candidates,search


def verify_feedback(placement,source,sta):
    meta=load_json(source/'manifest.json');sm=load_json(sta/'manifest.json');audit=load_json(sta/'handoff_audit.json')
    packed,placed=sha(placement/'packed.json'),sha(placement/'placement.json')
    if (meta['status']!='routed' or meta.get('vendor_pnr_executed') is not False or sm['status']!='imported'
            or sm.get('vendor_placement_executed') is not False or sm.get('vendor_routing_executed') is not False
            or meta['netlist_sha256']!=packed or sm['packed_sha256']!=packed
            or meta['placement_sha256']!=placed or sm['placement_sha256']!=placed
            or sm['routes_sha256']!=sha(source/'routes.json') or not sm['sta_completed']
            or audit['status']!='initial_handoff_checks_passed'
            or sm['handoff_audit_sha256']!=sha(sta/'handoff_audit.json') or audit['source_sha256']['probe.tr']!=sha(sta/'probe.tr')):
        raise ValueError('Critical tree source is not the matching audited implementation')
    return meta,sm,audit


def worker(out,placement,source,sta,count,seconds,explore_setup_ns=0.,cone_budgets=False,include_single_sink=False,search_mode='whole',pin_feedback=None,explore_hold_ns=0.,source_history=None,explore_current_wns=False):
    from native_device_session import NativeDeviceSession
    native=NativeDeviceSession(out)
    from ownpr_device import Device
    from ownpr_timing_graph import TimingRoutingGraph
    from ownpr_expand_routes import expand
    from ownpr_router import validate
    from ownpr_whole_net_eco import rebuild
    from ownpr_wide_ff_packing import configure_graph
    sys.path.insert(0,str(ROOT/'experiments'))
    from analyze_ownpr_critical_wires import analyze
    start=time.monotonic();deadline=start+seconds;design=load_json(placement/'packed.json');placed=load_json(placement/'placement.json')
    meta,sm,audit=verify_feedback(placement,source,sta)
    historical=None
    if source_history:
        review=load_json(source_history)['designs']
        if len(review)!=1:raise ValueError('Source reattachment requires one reviewed historical design')
        review=review[0]
        for name,digest in review['selected_source_sha256'].items():
            if sha(ROOT/name)!=digest:raise ValueError('Historical source checkpoint changed')
        hp={k:ROOT/v for k,v in review['selected'].items()};_,hm,_=verify_feedback(hp['placement'],hp['routes'],hp['sta'])
        if sha(hp['placement']/'packed.json')!=sha(placement/'packed.json') or hm['cst_sha256']!=sm['cst_sha256']:raise ValueError('Source reattachment requires identical logical netlist and package constraints')
        historical=dict(nets={n['name']:n for n in load_json(hp['routes']/'nets.json')},routes=load_json(hp['routes']/'routes.json'))
    ranking=analyze(sta,placement,source,32);nets=load_json(source/'nets.json');byname={n['name']:n for n in nets};routes=load_json(source/'routes.json')
    from run_ownpr_pin_closure import metric
    wns=metric(sta)['setup_wns_ns']
    graph=TimingRoutingGraph(Device(),ROOT/'results/native_rrg_001',native);configure_graph(graph,design,placed)
    graph.heuristic_scale=.03;graph.guided_corridor=True;graph.guide_edge_delay=.05
    owners={node:name for name,r in routes['nets'].items() for node in r['nodes']}
    tree=load_json(source/'clock_tree.json')
    if sm['clock_tree_sha256']!=sha(source/'clock_tree.json'):raise ValueError('Clock source changed')
    for row in tree['parents']:
        r,c,i=row['node']
        if min(r,c)>=1 and i in graph.device.names:
            node=graph.node(r-1,c-1,graph.device.names[i])
            if node in owners:raise ValueError('Clock/data occupancy conflict')
            owners[node]='__clock__'
    from ownpr_ioreg_macros import reserved_points
    for r,c,i in reserved_points(design,placed):
        node=graph.node(r-1,c-1,graph.device.names[i])
        if node in owners:raise ValueError('I/O register occupancy conflict')
        owners[node]='__ioreg__'
    capture_rows=load_json(sta/'sta_endpoint_audit.json');captures={(q['instance'],q['pin']):q for q in capture_rows}
    budgets,fallback=endpoint_budgets(graph,design,capture_rows,read_text(sta/'probe.tr'),cone_budgets)
    pin_info=None
    if pin_feedback:
        from ownpr_pin_feedback import load as load_pins,physical_budgets
        logical,pin_info=load_pins(pin_feedback,sta);precise=physical_budgets(graph,capture_rows,logical)
        budgets.update(precise);pin_info['physical_receivers']=len(precise)
        (out/'pin_budget_audit.json').write_text(json.dumps(pin_info,indent=2))
    def measure(net,record):
        physical=expand(graph,[net],dict(nets={net['name']:record}),native.fanouts)['nets'][net['name']]
        parents={tuple(p['node']):tuple(p['parent']) if p['parent'] else None for p in physical['parents']};values={}
        with native.parent_tree(parents):
            delays={p:(native.delay(p,1),native.delay(p,2)) for p in parents}
            for sink in physical['sinks']:
                chain=list(map(tuple,sink['path_sink_to_source']))
                values[sink['resource']]=[sum(delays[p][selector][i] for p in chain) for selector,i in ((0,2),(0,3),(1,0),(1,1))]
        return values
    trials=[];accepted=[];(out/'searches.json').write_text('[]\n')
    for candidate in ranking['ranked_nets']:
        if len(trials)>=count or time.monotonic()+15>deadline:break
        net=byname[candidate['net']]
        if net.get('clock') or net.get('constant_value') is not None or len(net['sinks'])<(1 if include_single_sink else 2):continue
        if historical and (net['name'] not in historical['nets'] or historical['nets'][net['name']]['source']==net['source']):continue
        targets=[];worst_slack,labels=critical_labels(candidate,ranking['traces'])
        for endpoint in labels:
            name,pin=endpoint.rsplit('/',1);q=captures.get((name,pin))
            if q and len(q['nodes'])==1:
                r,c,i=q['nodes'][0]['node'];node=graph.node(r-1,c-1,graph.device.names[i])
                if node in net['sinks']:targets.append(node)
        if not targets:continue
        current=routes['nets'][net['name']];before=measure(net,current)
        critical=max(targets,key=lambda s:max(before[s][:2]))
        if historical:
            from ownpr_source_reconnect import reconnect_source
            bounds={s:budgets.get(s,fallback) for s in net['sinks']};minimum=min(b.get('original_setup_slack_ns',b['setup']) for b in bounds.values())
            prioritized=dict(net,sink_criticalities={s:max(.05,1.-(b.get('original_setup_slack_ns',b['setup'])-minimum)/4.)**2 for s,b in bounds.items()})
            rows,search=reconnect_source(graph,historical['nets'][net['name']],historical['routes']['nets'][net['name']],prioritized,owners,min(deadline,time.monotonic()+100),max_depth=8)
            candidates=[r['record'] for r in rows];search['retained_candidates']=[{k:v for k,v in r.items() if k!='record'} for r in rows]
        else:candidates,search=search_candidates(graph,net,current,critical,owners,deadline,search_mode)
        bounds={s:budgets.get(s,fallback) for s in net['sinks']}
        row=dict(net=net['name'],driver=candidate['driver'],fanout=len(net['sinks']),critical_sinks=targets,critical_labels=labels,critical_path_slack_ns=worst_slack,baseline=before,sink_budgets=bounds,search=search,evaluations=[]);best=None;provisional=None
        for record in candidates:
            if time.monotonic()>deadline:break
            after=measure(net,record);ok,delta=eligible(before,after,targets,bounds)
            row['evaluations'].append(dict(eligible=ok,after=after,**delta))
            score=(delta['predicted_min_sink_setup_slack_ns'],delta['best_critical_gain_ns'],-len(record['pips']))
            if ok and (best is None or score>best[0]):best=(score,record,after,delta)
            if (exploratory(delta,explore_setup_ns) or hold_exploratory(delta,explore_hold_ns) or explore_current_wns and worst_slack_exploratory(delta,wns)) and (provisional is None or score>provisional[0]):provisional=(score,record,after,delta)
        # Preserve the original paths of receivers that exhausted their
        # endpoint budgets, then rebuild the other branches. Re-measure the
        # entire tree: retaining PIPs does not retain electrical delay.
        row['repair_rounds']=[];frozen=set()
        for _ in range(2):
            if best or historical or search_mode=='branch' or time.monotonic()+15>deadline:break
            # Protect the smallest repair set of one promising alternative;
            # unioning failures from unrelated alternatives can freeze most
            # of the original tree and prevent any useful restructuring.
            failures=[e for e in row['evaluations'] if {v['sink'] for v in e['budget_violations']}-frozen]
            if not failures:break
            chosen=min(failures,key=lambda e:(len({v['sink'] for v in e['budget_violations']}-frozen),-e['predicted_min_sink_setup_slack_ns'],-e['best_critical_gain_ns']))
            additional={v['sink'] for v in chosen['budget_violations']}-frozen
            if not additional or len(frozen|additional)==len(net['sinks']):break
            frozen.update(additional)
            from ownpr_incremental_routes import retained_branches
            blocked={n for n,owner in owners.items() if owner!=net['name']}
            seeds,retention=retained_branches(graph,[dict(net,sinks=sorted(frozen))],dict(nets={net['name']:current}),[net],[net],blocked)
            if net['name'] not in seeds or set(seeds[net['name']]['retained_sinks'])!=frozen:raise ValueError('Protected receiver paths were not retained')
            repaired,search=rebuild(graph,net,critical,owners,min(deadline,time.monotonic()+55),seconds_per_variant=25,root_weights=(4.,8.),initial_tree=seeds[net['name']])
            row['repair_rounds'].append(dict(protected_sinks=sorted(frozen),retention=retention,search=search))
            for record in repaired:
                after=measure(net,record);ok,delta=eligible(before,after,targets,bounds)
                row['evaluations'].append(dict(eligible=ok,after=after,protected_sinks=sorted(frozen),**delta))
                score=(delta['predicted_min_sink_setup_slack_ns'],delta['best_critical_gain_ns'],-len(record['pips']))
                if ok and (best is None or score>best[0]):best=(score,record,after,delta)
                if (exploratory(delta,explore_setup_ns) or hold_exploratory(delta,explore_hold_ns) or explore_current_wns and worst_slack_exploratory(delta,wns)) and (provisional is None or score>provisional[0]):provisional=(score,record,after,delta)
        row['provisional_only']=best is None and provisional is not None
        if best is None and provisional is not None:best=provisional
        if best:
            _,record,after,delta=best
            for node in current['nodes']:owners.pop(node,None)
            for node in record['nodes']:owners[node]=net['name']
            routes['nets'][net['name']]=record;accepted.append(dict(net=net['name'],provisional_only=row['provisional_only'],**delta));row['locally_selected']=True
        else:row['locally_selected']=False
        trials.append(row);(out/'searches.json').write_text(json.dumps(trials,indent=2));print(net['name'],row['locally_selected'],len(candidates),flush=True)
        if best:break
    check=validate(graph,nets,routes)
    for file in ('nets.json','all_nets.json','clock_tree.json','static_connections.json'):shutil.copyfile(source/file,out/file)
    if (source/'hard_endpoint_audit.json').exists():shutil.copyfile(source/'hard_endpoint_audit.json',out/'hard_endpoint_audit.json')
    routes.update(status='routed',timing_signoff=False);(out/'routes.json').write_text(json.dumps(routes))
    static_keys=('netlist_sha256','placement_sha256','vendor_pnr_executed','open_chip_database_used','net_count','native_source_sha256','dll_sha256','dedicated_clock','carry_capacity_audit','wide_mux_capacity_audit','ioreg_capacity_audit','static_configuration_pending','hard_endpoint_audit_sha256','hard_cells')
    result=dict({k:meta[k] for k in static_keys if k in meta},status='routed',critical_tree_rebuild=accepted,searches_sha256=sha(out/'searches.json'),connectivity_audit=check,
        source_routes_sha256=sha(source/'routes.json'),source_sta_sha256=sha(sta/'probe.tr'),wall_seconds=time.monotonic()-start,
        source_parent_manifest_sha256=sha(source/'manifest.json'),producer_source_sha256={p:sha(out/p) for p in ('worker_source.py.txt','whole_net_source.py.txt','cone_budget_source.py.txt','branch_source.py.txt')},
        heuristic_scale=.03,search_bias=30.,root_arrival_weight=1.,guided_corridor=True,guide_edge_delay_ns_surrogate=.05,
        root_arrival_weight_sweep=[0.,4.,8.],explore_setup_limit_ns=explore_setup_ns,explore_hold_limit_ns=explore_hold_ns,timing_signoff=False,full_design_sta_required=True,
        local_budgets_satisfied=all(not r['provisional_only'] for r in accepted),cone_budgets=cone_budgets,include_single_sink=include_single_sink,search_mode=search_mode,pin_feedback=pin_info,
        explore_current_wns=explore_current_wns,source_setup_wns_ns=wns)
    if pin_feedback:result['producer_source_sha256']['pin_budget_source.py.txt']=sha(out/'pin_budget_source.py.txt')
    if source_history:result.update(source_history_sha256=sha(source_history),source_reattachment=True,source_reconnect_sha256=sha(out/'source_reconnect.py.txt'))
    (out/'manifest.json').write_text(json.dumps(result,indent=2));print('selected_routes',len(accepted),flush=True)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for n in ('placement','routes','sta','out'):p.add_argument('--'+n,type=Path,required=True)
    p.add_argument('--worker',action='store_true');p.add_argument('--count',type=int,default=2);p.add_argument('--seconds',type=int,default=240);p.add_argument('--explore-setup-ns',type=float,default=0.)
    p.add_argument('--cone-budgets',action='store_true',help='Use conservative structural endpoint bounds for supported internal sinks')
    p.add_argument('--explore-hold-ns',type=float,default=0.,help='Explicit infeasible candidate with bounded predicted hold loss; requires subsequent hold repair and complete STA')
    p.add_argument('--pin-feedback',type=Path,help='Checked native worst-through-pin setup/hold capture for this exact implementation')
    p.add_argument('--include-single-sink',action='store_true',help='Also search ordinary point-to-point nets')
    p.add_argument('--search-mode',choices=('whole','branch','both'),default='whole',help='Retain other branches or rebuild the whole net; all candidates require loaded-tree measurement')
    p.add_argument('--source-history',type=Path,help='Reconnect moved drivers to historical downstream fragments')
    p.add_argument('--explore-current-wns',action='store_true',help='Propose bounded setup trades within current negative WNS; full STA remains mandatory')
    a=p.parse_args();out=a.out.resolve()
    if not 1<=a.count<=3 or not 1<=a.seconds<=300:p.error('Bounded search requires count 1..3, seconds 1..300')
    if not 0<=a.explore_setup_ns<=.5:p.error('Exploration is bounded to at most 0.5 ns predicted setup failure; never a qualification override')
    if not 0<=a.explore_hold_ns<=1:p.error('Hold exploration must be bounded to at most 1 ns')
    if a.explore_current_wns and not a.pin_feedback:p.error('WNS exploration requires checked pin feedback')
    if a.worker:return worker(out,a.placement.resolve(),a.routes.resolve(),a.sta.resolve(),a.count,a.seconds,a.explore_setup_ns,a.cone_budgets,a.include_single_sink,a.search_mode,a.pin_feedback,a.explore_hold_ns,a.source_history,a.explore_current_wns)
    from gowin_probe import prepare
    reserve(ROOT,200_000_000);prepare(out);shutil.copyfile(__file__,out/'worker_source.py.txt');shutil.copyfile(ROOT/'scripts/ownpr_whole_net_eco.py',out/'whole_net_source.py.txt')
    shutil.copyfile(ROOT/'scripts/ownpr_cone_budgets.py',out/'cone_budget_source.py.txt')
    shutil.copyfile(ROOT/'scripts/ownpr_branch_reconnect.py',out/'branch_source.py.txt')
    if a.pin_feedback:shutil.copyfile(ROOT/'scripts/ownpr_pin_feedback.py',out/'pin_budget_source.py.txt')
    if a.source_history:shutil.copyfile(ROOT/'scripts/ownpr_source_reconnect.py',out/'source_reconnect.py.txt')
    python=ROOT/'binary_audit/flow/runtime/bin/python.exe';start=time.monotonic()
    rc=None;expired=False
    with (out/'worker.log').open('w') as log:
        try:rc=subprocess.run([str(python),str(Path(__file__).resolve()),'--worker','--placement',str(a.placement.resolve()),'--routes',str(a.routes.resolve()),'--sta',str(a.sta.resolve()),'--out',str(out),'--count',str(a.count),'--seconds',str(a.seconds),'--explore-setup-ns',str(a.explore_setup_ns),'--explore-hold-ns',str(a.explore_hold_ns),'--search-mode',a.search_mode]+(['--cone-budgets'] if a.cone_budgets else [])+(['--include-single-sink'] if a.include_single_sink else [])+(['--pin-feedback',str(a.pin_feedback.resolve())] if a.pin_feedback else [])+(['--source-history',str(a.source_history.resolve())] if a.source_history else [])+(['--explore-current-wns'] if a.explore_current_wns else []),cwd=ROOT,env=dict(os.environ,PYTHONHOME=sys.base_prefix),stdout=log,stderr=subprocess.STDOUT,timeout=a.seconds+60).returncode
        except subprocess.TimeoutExpired:expired=True
    if rc!=0:
        (out/'manifest.json').write_text(json.dumps(dict(status='timeout' if expired else 'failed',returncode=rc,wall_seconds=time.monotonic()-start,vendor_pnr_executed=False),indent=2))
        raise SystemExit('Tree search failed; see worker.log')
    print('complete',time.monotonic()-start)


if __name__=='__main__':main()
