"""Bounded, fixed-placement route ECO driven by native final STA violations.

Changes only ordinary PIPs selected by our search. Every candidate is checked
for physical connectivity and evaluated on its full native parent tree. The
native placer/router are never invoked. Final whole-design STA remains required.
"""
from ownpr_image_storage import read_text as read_artifact_text
import argparse,copy,json,os,re,shutil,subprocess,sys,time
from collections import defaultdict
from pathlib import Path
from gowin_probe import ROOT,prepare
from ownpr_image_storage import sha,load_json
from ownpr_timing_numbers import negative_slack


def violations(text,kind):
    section=re.search(r'^3\.1\.[12] '+kind+r' Paths Table\n(.*?)(?=\n3\.)',text,re.S|re.M)
    if not section:raise ValueError('Missing final STA table')
    return [(float(s),a,b) for s,a,b in re.findall(r'^\s*\d+\s+([-\d.]+)\s+(own_i_\d+/\S+)\s+(own_i_\d+/\S+)\s+',section[1],re.M) if negative_slack(s)]


def hold_first_wires(text):
    """Parse complete hold traces once; preserve ambiguity for callers to reject."""
    from extract_timing import ROW
    section=re.search(r'^3\.3\.2 Hold Analysis Report\n(.*?)(?=^3\.|\Z)',text,re.M|re.S)
    if not section:raise ValueError('Missing complete hold traces')
    found=defaultdict(list)
    for block in re.split(r'^\s*Path\d+\s*$',section[1],flags=re.M):
        slack=re.search(r'^Slack\s*:\s*([-\d.]+)',block,re.M)
        data=re.search(r'Data Arrival Path:\n(.*?)(?=\nData Required Path:)',block,re.S)
        if not slack or not negative_slack(slack[1]) or not data:continue
        rows=[m.groups() for line in data[1].splitlines() if (m:=ROW.match(line))]
        if not rows:continue
        launch=next((i for i,row in enumerate(rows) if row[2]=='tC2Q'),None)
        if launch is not None:rows=rows[launch:]
        wires=[row[-1] for row in rows if row[2]=='tNET']
        if not wires:raise ValueError('Hold violation has no ordinary data branch')
        found[(rows[0][-1],rows[-1][-1])].append(wires[0])
    return dict(found)


def first_hold_wire(text,launch,capture):
    """Resolve the actual first data branch of one reported violating path."""
    found=hold_first_wires(text).get((launch,capture),[])
    if len(found)!=1:raise ValueError('Ambiguous or missing hold path branch')
    return found[0]


def worker(out,source,sta,placement,setup_path_limit=1,whole_net_count=0):
    from native_device_session import NativeDeviceSession
    from ownpr_device import Device,RoutingGraph
    from ownpr_expand_routes import expand
    from ownpr_router import validate
    start=time.monotonic();native=NativeDeviceSession(out);graph=RoutingGraph(Device(),ROOT/'results/native_rrg_001')
    nets=json.loads((source/'nets.json').read_text());byname={n['name']:n for n in nets};routes=json.loads((source/'routes.json').read_text())
    meta=json.loads((source/'manifest.json').read_text());places=json.loads((placement/'placement.json').read_text());cells=json.loads((placement/'packed.json').read_text())['modules']['top']['cells']
    aliases=json.loads((sta/'name_map.json').read_text())['instances'];reverse={v:k for k,v in aliases.items()};text=read_artifact_text(sta/'probe.tr')
    checked=json.loads((sta/'handoff_audit.json').read_text())
    if checked['status']!='initial_handoff_checks_passed' or checked['source_sha256']['probe.tr']!=sha(sta/'probe.tr'):raise ValueError('Unverified STA feedback')
    sta_meta=json.loads((sta/'manifest.json').read_text())
    if sta_meta.get('routes_sha256') and sta_meta['routes_sha256']!=sha(source/'routes.json'):raise ValueError('STA feedback belongs to another route')
    if meta['netlist_sha256']!=sha(placement/'packed.json') or meta['placement_sha256']!=sha(placement/'placement.json'):raise ValueError('ECO placement differs from route source')
    owners={node:name for name,r in routes['nets'].items() for node in r['nodes']};targets=[]
    from ownpr_ioreg_macros import reserved_points
    for r,c,i in reserved_points({'modules':{'top':{'cells':cells}}},places):
        node=graph.node(r-1,c-1,graph.device.names[i])
        if node in owners:raise ValueError('Dedicated I/O output overlaps ordinary ECO input routes')
        owners[node]='__reserved_ioreg_output_or_static_control__'
    if (source/'clock_tree.json').exists():
        if sta_meta.get('clock_tree_sha256')!=sha(source/'clock_tree.json'):raise ValueError('STA feedback uses another dedicated clock tree')
        tree=json.loads((source/'clock_tree.json').read_text())
        for row in tree['parents']:
            r,c,i=row['node']
            if min(r,c)<1 or i not in graph.device.names:continue
            node=graph.node(r-1,c-1,graph.device.names[i])
            if node in owners:raise ValueError('Dedicated clock overlaps ordinary ECO input routes')
            owners[node]='__reserved_dedicated_clock__'
    for slack,launch,capture in violations(text,'Hold'):
        name,pin=launch.split('/');original=reverse[name]
        if cells[original]['type']!='IBUF':raise ValueError('Initial hold ECO supports IBUF launch only')
        bit=cells[original]['connections']['O'][0];net='net_'+str(bit)
        if len(byname[net]['sinks'])==1:sink=byname[net]['sinks'][0]
        else:
            label=first_hold_wire(text,launch,capture);alias,pin=label.split('/');receiver=reverse[alias]
            captures=load_json(sta/'sta_endpoint_audit.json')
            matches=[r for r in captures if r['native_name']+'/'+r['pin']==label]
            if len(matches)!=1 or len(matches[0]['nodes'])!=1 or cells[receiver]['connections'][pin]!=[bit]:raise ValueError('Hold branch is not driven by its reported input net')
            r,c,i=matches[0]['nodes'][0]['node'];sink=graph.node(r-1,c-1,graph.device.names[i])
            if sink not in byname[net]['sinks']:raise ValueError('Hold branch differs from routed receiver')
        targets.append(dict(net=net,sink=sink,kind='hold',improvement=-slack+.035))
    setup=violations(text,'Setup');setup_progress=None
    if setup:
        from ownpr_eco_feedback import setup_paths,SetupProgress
        from ownpr_placement_feedback import load as checked_feedback
        checked_feedback(sta,json.loads((placement/'packed.json').read_text()))
        captures=load_json(sta/'sta_endpoint_audit.json');points={r['native_name']+'/'+r['pin']:r for r in captures}
        def resolve(label):
            alias,pin=label.split('/');original=reverse[alias]
            if label not in points:
                if cells[original]['type']=='ALU' and pin=='CIN':return None
                raise ValueError('Missing physical endpoint for setup feedback')
            if not points[label]['nodes']:return None
            if pin=='I' and any(r['iobuf']==original for r in places.get('ioreg_clusters',{}).values()):return None
            if cells[original]['port_directions'].get(pin)!='input':raise ValueError('Setup wire does not end on an input')
            bit=cells[original]['connections'][pin][0];r,c,i=points[label]['nodes'][0]['node'];sink=graph.node(r-1,c-1,graph.device.names[i]);net='net_'+str(bit)
            if net not in byname or sink not in byname[net]['sinks']:raise ValueError('Setup endpoint differs from ordinary route')
            return net,sink
        paths=setup_paths(text,checked['timing']['path_coverage']['setup']['paths'])
        setup_progress=SetupProgress(paths,resolve,setup_path_limit);targets.extend(setup_progress.targets)
    def endpoints(record):
        adj=defaultdict(list);incoming={}
        for pip in record['pips']:
            r,c,a,b=pip;u=graph.node(r,c,a);v=graph.node(r,c,b);adj[u].append(v);incoming[v]=(u,pip)
        return adj,incoming
    def measure(name,record):
        physical=expand(graph,[byname[name]],dict(nets={name:record}),native.fanouts)['nets'][name]
        parents={tuple(r['node']):tuple(r['parent']) if r['parent'] else None for r in physical['parents']};values={}
        with native.parent_tree(parents):
            cache={p:(native.delay(p,1),native.delay(p,2)) for p in parents}
            for sink in physical['sinks']:
                chain=[tuple(p) for p in sink['path_sink_to_source']]
                values[sink['resource']]=(max(sum(cache[p][0][i] for p in chain) for i in (2,3)),min(sum(cache[p][1][i] for p in chain) for i in (0,1)),sum(cache[p][0][2] for p in chain),sum(cache[p][0][3] for p in chain))
        return values
    changes=[];branch_graph=None;branch_searches=[];whole_net_searches=[];rebuilt=set()
    for target in targets:
        if time.monotonic()-start>240:raise TimeoutError('ECO wall budget')
        if target['kind']=='setup' and not setup_progress.pending(target):continue
        name=target['net'];record=routes['nets'][name];baseline=measure(name,record);sink=target['sink'];adj,incoming=endpoints(record)
        chain=[sink]
        while chain[-1] in incoming:chain.append(incoming[chain[-1]][0])
        candidates=[];seen=set();evaluations=0
        # A small local replacement window preserves every other tree branch.
        for i in range(len(chain)-1):
            for width in (1,2,3,4,5):
                if i+width>=len(chain):continue
                segment=list(reversed(chain[i:i+width+1]));u,v=segment[0],segment[-1]
                if any(len(adj[n])!=1 for n in segment[1:-1]):continue
                removed=[incoming[n][1] for n in segment[1:]];free=set(segment[1:-1]);blocked=set(record['nodes'])-free-{u,v}
                def allowed(node):return node not in blocked and (node not in owners or owners[node]==name)
                up=defaultdict(list)
                for n,_,pip in graph.uphill(v):
                    if n==u or allowed(n):up[n].append(pip)
                alternatives=[]
                if u in up:alternatives.extend([[p] for p in up[u]])
                for a,_,pa in graph.downhill(u):
                    if a in (u,v) or not allowed(a):continue
                    alternatives.extend([[pa,pb] for pb in up.get(a,[])])
                    for b,_,pb in graph.downhill(a):
                        if b in (u,v,a) or not allowed(b):continue
                        alternatives.extend([[pa,pb,pc] for pc in up.get(b,[])])
                        for c,_,pc in graph.downhill(b):
                            if c in (u,v,a,b) or not allowed(c):continue
                            alternatives.extend([[pa,pb,pc,pd] for pd in up.get(c,[])])
                for path in alternatives:
                    key=tuple(tuple(p) for p in path)
                    if key==tuple(tuple(p) for p in removed) or (i,width,key) in seen:continue
                    seen.add((i,width,key));pips=[p for p in record['pips'] if p not in removed]+list(path)
                    used={byname[name]['source']}
                    for r,c,a,b in pips:used.update((graph.node(r,c,a),graph.node(r,c,b)))
                    candidate=dict(pips=pips,nodes=sorted(used),parent={},estimated_arrival_cost={},cost_units='whole_tree_native_evaluation')
                    try:measured=measure(name,candidate)
                    except ValueError:continue
                    evaluations+=1
                    before=baseline[sink];after=measured[sink]
                    if target['kind']=='hold':
                        gain=after[1]-before[1]
                        # Slow/fast ratios differ by resource. A fixed 1 ns
                        # slow-corner cap can exclude every valid repair of a
                        # 0.5 ns fast-corner failure. Rank the slow penalty and
                        # let the mandatory complete STA acceptance gate decide.
                        if gain<target['improvement']:continue
                        if any(measured[s][0]>baseline[s][0]+.03 or measured[s][1]<baseline[s][1]-.000001 for s in measured if s!=sink):continue
                        score=(after[0]-before[0],gain,len(pips))
                    else:
                        gain=setup_progress.gain(target,before,after)
                        if gain<.001 or any(measured[s][0]>baseline[s][0]+.03 or measured[s][1]<baseline[s][1]-.01 for s in measured if s!=sink):continue
                        score=(-gain,len(pips))
                    candidates.append((score,candidate,measured,gain))
                if evaluations>250:break
            if evaluations>250:break
        if target['kind']=='setup':
            from ownpr_branch_reconnect import reconnect
            from ownpr_timing_graph import TimingRoutingGraph
            if branch_graph is None:branch_graph=TimingRoutingGraph(graph.device,ROOT/'results/native_rrg_001',native)
            reconnections,stats=reconnect(branch_graph,byname[name],record,sink,owners,start+240)
            branch_searches.append(dict(net=name,**stats))
            for candidate in reconnections:
                try:measured=measure(name,candidate)
                except ValueError:continue
                evaluations+=1;gain=setup_progress.gain(target,baseline[sink],measured[sink])
                if gain<.001 or any(measured[s][0]>baseline[s][0]+.03 or measured[s][1]<baseline[s][1]-.01 for s in measured if s!=sink):continue
                candidates.append(((-gain,len(candidate['pips'])),candidate,measured,gain))
            if len(rebuilt)<whole_net_count and name not in rebuilt and len(byname[name]['sinks'])>1:
                from ownpr_whole_net_eco import rebuild
                rebuilt.add(name)
                rebuilt_candidates,stats=rebuild(branch_graph,byname[name],sink,owners,min(start+220,time.monotonic()+55))
                stats.update(net=name,critical_sink=sink,evaluations=[])
                for candidate in rebuilt_candidates:
                    try:measured=measure(name,candidate)
                    except ValueError as error:
                        stats['evaluations'].append(dict(status='physical_tree_rejected',reason=str(error)));continue
                    evaluations+=1;gain=setup_progress.gain(target,baseline[sink],measured[sink])
                    setup_regression=max((measured[s][0]-baseline[s][0] for s in measured if s!=sink),default=0.)
                    hold_loss=max((baseline[s][1]-measured[s][1] for s in measured if s!=sink),default=0.)
                    eligible=gain>=.001 and setup_regression<=.03 and hold_loss<=.01
                    stats['evaluations'].append(dict(status='locally_eligible' if eligible else 'local_timing_rejected',
                        target_gain_ns=gain,other_sink_max_setup_regression_ns=setup_regression,other_sink_max_hold_loss_ns=hold_loss,
                        before=baseline[sink],after=measured[sink]))
                    if eligible:candidates.append(((-gain,len(candidate['pips'])),candidate,measured,gain))
                whole_net_searches.append(stats)
                print('ECO_WHOLE_NET',json.dumps(stats),flush=True)
        if candidates:
            _,best,values,gain=min(candidates,key=lambda c:c[0]);routes['nets'][name]=best
            for node in record['nodes']:owners.pop(node,None)
            for node in best['nodes']:owners[node]=name
            if setup_progress is not None:setup_progress.update(name,baseline,values)
            change=dict(**target,gain_ns=gain,before=baseline[sink],after=values[sink],candidates_evaluated=evaluations,
                old_pips=len(record['pips']),new_pips=len(best['pips']))
            changes.append(change);print('ECO_ACCEPT',json.dumps(change),flush=True)
        else:print('ECO_NO_CANDIDATE',json.dumps(target),'evaluated',evaluations,flush=True)
    legality=validate(graph,nets,routes);routes.update(status='routed',timing_signoff=False,scope='Own local and bounded whole-net route ECO; final full STA required')
    (out/'routes.json').write_text(json.dumps(routes));(out/'nets.json').write_text(json.dumps(nets,indent=2))
    for file in ('all_nets.json','clock_tree.json','static_connections.json'):
        if (source/file).exists():shutil.copyfile(source/file,out/file)
    meta={k:meta[k] for k in ('netlist_sha256','placement_sha256','vendor_pnr_executed','open_chip_database_used','net_count','native_source_sha256','dll_sha256','dedicated_clock','carry_capacity_audit','wide_mux_capacity_audit','ioreg_capacity_audit','static_configuration_pending') if k in meta}
    meta.update(status='routed',connectivity_audit=legality,eco_changes=changes,branch_searches=branch_searches,eco_source_routes_sha256=sha(source/'routes.json'),
        feedback_report_sha256=sha(sta/'probe.tr'),wall_seconds=time.monotonic()-start,timing_signoff=False,bitstream_generated=False)
    meta['setup_path_limit']=setup_path_limit
    meta.update(whole_net_limit=whole_net_count,whole_net_searches=whole_net_searches)
    if whole_net_count:meta['whole_net_helper_sha256']=sha(Path(__file__).with_name('ownpr_whole_net_eco.py'))
    if setup_progress is not None:
        meta['setup_path_progress']=setup_progress.report()
        meta['setup_feedback_source_sha256']=sha(Path(__file__).with_name('ownpr_eco_feedback.py'))
    (out/'manifest.json').write_text(json.dumps(meta,indent=2))
    print('ECO_COMPLETE',len(changes),time.monotonic()-start,flush=True)


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--out',type=Path,required=True);ap.add_argument('--worker',action='store_true')
    ap.add_argument('--routes',type=Path,default=ROOT/'results/ownpr_int2float_route_008');ap.add_argument('--sta',type=Path,default=ROOT/'results/native_ownpr_export_012');ap.add_argument('--placement',type=Path,default=ROOT/'results/ownpr_int2float_place_004')
    ap.add_argument('--setup-paths',type=int,default=1,help='Up to 100 failing setup paths with transition-aware per-path budgets')
    ap.add_argument('--whole-net-count',type=int,default=0,help='Rebuild up to 4 critical ordinary nets, freezing other resources');a=ap.parse_args()
    if not 1<=a.setup_paths<=100:ap.error('--setup-paths must be 1..100')
    if not 0<=a.whole_net_count<=4:ap.error('--whole-net-count must be 0..4')
    out=a.out.resolve();source=a.routes.resolve();sta=a.sta.resolve();placement=a.placement.resolve()
    if a.worker:return worker(out,source,sta,placement,a.setup_paths,a.whole_net_count)
    from workspace_budget import reserve
    reserve(ROOT,500_000_000)
    prepare(out);shutil.copyfile(__file__,out/'eco_source.py.txt');python=ROOT/'binary_audit/flow/runtime/bin/python.exe'
    with (out/'worker.log').open('w') as log:
        try:rc=subprocess.run([str(python),str(Path(__file__).resolve()),'--worker','--out',str(out),'--routes',str(source),'--sta',str(sta),'--placement',str(placement),'--setup-paths',str(a.setup_paths),'--whole-net-count',str(a.whole_net_count)],cwd=python.parent,env=dict(os.environ,PYTHONHOME=sys.base_prefix),stdout=log,stderr=subprocess.STDOUT,timeout=270).returncode
        except subprocess.TimeoutExpired:rc=-1
    if rc:raise SystemExit('ECO failed; see '+str(out/'worker.log'))
    print((out/'manifest.json').read_text()[:1000])


if __name__=='__main__':main()
