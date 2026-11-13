"""Own latency-driven dedicated clock search on vendor-derived resources.

Enumerate the small ancestor DAG of fixed endpoints, query complete paths,
then merge compatible paths. Unimplemented encoding choices remain excluded.
"""
from collections import defaultdict,deque,Counter
import ctypes as C
import json,math,struct,time
from ownpr_clock_configuration import ClockChoices
from audit_native_wire_delays import f32


def candidate_space(graph,device,source,sinks,blocked=(),max_paths=20000):
    root=graph.by_coordinate[tuple(source)];targets={graph.by_coordinate[tuple(s)] for s in sinks};reverse=defaultdict(list)
    for a,bs in graph.adj.items():
        for b in bs:reverse[b].append(a)
    ancestors=set(targets);todo=list(targets)
    while todo:
        b=todo.pop()
        if b==root:continue
        for a in reverse[b]:
            if a not in ancestors:ancestors.add(a);todo.append(a)
    choices=ClockChoices(device,graph)
    choices.selected_parents={tuple(graph.nodes[n]['coordinate']):choices.primary_alias_parent(tuple(graph.nodes[n]['coordinate'])) for n in ancestors}
    blocked={tuple(n) for n in blocked};adj=defaultdict(list);reach={root};todo=[root];rejected=Counter()
    if tuple(source) in blocked or any(tuple(s) in blocked for s in sinks):raise ValueError('Clock endpoint overlaps routed data')
    while todo:
        a=todo.pop()
        for b in graph.adj[a]:
            if b not in ancestors or tuple(graph.nodes[b]['coordinate']) in blocked:continue
            try:choices.edge(graph.nodes[a]['coordinate'],graph.nodes[b]['coordinate'])
            except ValueError as exc:rejected[str(exc)]+=1;continue
            adj[a].append(b)
            if b not in reach:reach.add(b);todo.append(b)
    if not targets<=reach:raise ValueError('Known clock encoding cannot reach every fixed sink')
    degree=Counter(b for bs in adj.values() for b in bs);queue=deque(sorted(n for n in reach if not degree[n]));ordered=[]
    while queue:
        a=queue.popleft();ordered.append(a)
        for b in adj[a]:
            degree[b]-=1
            if not degree[b]:queue.append(b)
    if len(ordered)!=len(reach):raise ValueError('Clock search requires an acyclic supported ancestor graph')
    prefixes={root:[(root,)]}
    for a in ordered:
        for b in adj[a]:
            paths=prefixes.setdefault(b,[]);paths.extend(p+(b,) for p in prefixes.get(a,[]))
            if len(paths)>max_paths:raise ValueError('Clock path enumeration budget exceeded')
    paths={t:prefixes[t] for t in sorted(targets)}
    if sum(map(len,paths.values()))>max_paths:raise ValueError('Total clock path enumeration budget exceeded')
    return paths,dict(ancestor_nodes=len(ancestors),supported_nodes=len(reach),supported_edges=sum(map(len,adj.values())),
        sinks=len(targets),candidate_paths=sum(map(len,paths.values())),rejected_encoding_edges=dict(rejected))


def make_tree(graph,source,paths):
    parents={tuple(source):None}
    for path in paths:
        for i,n in enumerate(path):
            coordinate=tuple(graph.nodes[n]['coordinate']);parent=tuple(graph.nodes[path[i-1]]['coordinate']) if i else None
            if coordinate in parents and parents[coordinate]!=parent:raise ValueError('Conflicting clock parents')
            parents[coordinate]=parent
    sink_to_source=[[graph.nodes[n]['coordinate'] for n in reversed(p)] for p in paths]
    connectivity=graph.audit_paths(sink_to_source)
    if connectivity['status']!='clock_paths_match':raise ValueError('Clock tree includes a non-native or empty path')
    return dict(status='clock_topology_candidate',source=list(source),parents=[dict(node=list(n),parent=list(p) if p else None,
        name=graph.nodes[graph.by_coordinate[n]]['name'],namespace=graph.nodes[graph.by_coordinate[n]]['namespace']) for n,p in sorted(parents.items())],
        paths_sink_to_source=sink_to_source,topology_sha256=graph.sha256,connectivity_audit=connectivity,
        clock_policy='AUTO',configuration_verified=False,resource_capacity_verified=False,timing_signoff=False,
        scope='Own complete-path delay search and compatible tree merge; final encoding, full I/O STA and device modes remain separate gates.')


def resolve_candidates(native,graph,paths,source):
    edges=defaultdict(set);wanted=set()
    for options in paths.values():
        for path in options:
            wanted.update(path)
            for a,b in zip(path,path[1:]):edges[a].add(b)
    root=graph.by_coordinate[tuple(source)];pointers={root:native.node(tuple(source))};queue=deque([root])
    while queue:
        a=queue.popleft();node=pointers[a];count=C.c_short.from_address(node+6).value
        if not 0<=count<=16000:raise ValueError('Invalid native clock fanout')
        matches=defaultdict(set)
        for i in range(count):
            p=native.getout(node,i)
            if p:matches[struct.unpack('<3h',C.string_at(p,6))].add(p)
        for b in edges[a]:
            found=matches[tuple(graph.nodes[b]['coordinate'])]
            if len(found)!=1:raise ValueError('Candidate clock edge is not a unique native connection')
            p=next(iter(found))
            if b in pointers:
                if pointers[b]!=p:raise ValueError('Clock coordinate aliases different physical resources')
            else:pointers[b]=p;queue.append(b)
    if set(pointers)!=wanted:raise ValueError('Unresolved clock candidate resource')
    return pointers


def measure(native,graph,paths,pointers):
    parents={}
    for path in paths:
        for i,n in enumerate(path):
            key=tuple(graph.nodes[n]['coordinate']);parent=tuple(graph.nodes[path[i-1]]['coordinate']) if i else None
            if key in parents and parents[key]!=parent:raise ValueError('Conflicting measured clock parents')
            parents[key]=parent
    resolved={p:pointers[graph.by_coordinate[p]] for p in parents};values={}
    with native.resolved_parent_tree(parents,resolved):
        for p,node in resolved.items():values[p]=[native.delay_pointer(node,s) for s in range(3)]
    sinks=[]
    for path in paths:
        sums=[[0.]*4 for _ in range(3)]
        for n in reversed(path):
            for s in range(3):sums[s]=[f32(a+b) for a,b in zip(sums[s],values[tuple(graph.nodes[n]['coordinate'])][s])]
        sinks.append(dict(node=graph.nodes[path[-1]]['coordinate'],delays_by_selector=sums,setup_wire_ns=max(sums[1][2:]),hold_wire_ns=min(sums[2][:2])))
    return sinks,values


def merged_load_changes(predicted,measured):
    def checked(rows):
        result={}
        for r in rows:
            node=tuple(r['node']);v=r['delays_by_selector']
            if node in result or len(v)!=3 or any(len(x)!=4 for x in v) or any(not math.isfinite(x) or x<0 for xs in v for x in xs):raise ValueError('Invalid or duplicate clock measurements')
            result[node]=r
        return result
    before,after=checked(predicted),checked(measured)
    if set(before)!=set(after):raise ValueError('Merged clock measurement changed endpoints')
    return [dict(node=list(n),isolated_delays_by_selector=before[n]['delays_by_selector'],merged_delays_by_selector=after[n]['delays_by_selector'],
        slow_rise_change_ns=after[n]['delays_by_selector'][1][2]-before[n]['delays_by_selector'][1][2])
        for n in sorted(before) if before[n]['delays_by_selector']!=after[n]['delays_by_selector']]


def search(native,graph,device,baseline,blocked,base_bits,timeout=120,slack_model=None,slack_trials=256):
    started=time.monotonic();source=baseline['source'];sinks=[p[0] for p in baseline['paths_sink_to_source']]
    paths,space=candidate_space(graph,device,source,sinks,blocked);pointers=resolve_candidates(native,graph,paths,source)
    costs={};choices=ClockChoices(device,graph);rejected=Counter()
    for target,options in paths.items():
        costs[target]=[]
        for path in options:
            if time.monotonic()-started>timeout:raise TimeoutError('Clock search budget exceeded')
            tree=make_tree(graph,source,[path])
            try:bits,info=choices.requirements(tree)
            except ValueError as error:
                rejected['unsupported_path_context: '+str(error)]+=1;continue
            if info['conflicts'] or any(b in base_bits and base_bits[b]!=v for b,v in bits.items()):rejected['configuration_conflict']+=1;continue
            result,_=measure(native,graph,[path],pointers);row=result[0]
            costs[target].append((row['setup_wire_ns'],row['delays_by_selector'][1][2],len(path),path,row,bits))
        if not costs[target]:raise ValueError('All clock paths conflict with current configuration')
        costs[target].sort(key=lambda x:x[:4])
    # Hardest endpoint first. Greedy compatible merge is explicit, not a
    # claim of global optimality. A final full-tree query catches loading or
    # parent-context differences from isolated candidate measurements.
    selected=[];selected_parents={};bits=dict(base_bits);predictions=[]
    for target in ([] if slack_model is not None else sorted(costs,key=lambda t:(-costs[t][0][0],t))):
        for _,_,_,path,row,requirements in costs[target]:
            parents={n:path[i-1] if i else None for i,n in enumerate(path)}
            if any(n in selected_parents and selected_parents[n]!=p for n,p in parents.items()):continue
            if any(b in bits and bits[b]!=v for b,v in requirements.items()):continue
            selected.append(path);selected_parents.update(parents);bits.update(requirements);predictions.append(row);break
        else:raise ValueError('No compatible clock tree under current greedy order')
    slack_report=None
    if slack_model is not None:
        from ownpr_clock_slack_search import optimize
        selected,result,node_values,slack_report=optimize(native,graph,device,baseline,costs,pointers,base_bits,slack_model,started+timeout,max_trials=slack_trials)
        predictions=[next(x[4] for x in costs[p[-1]] if x[3]==p) for p in selected]
    else:result,node_values=measure(native,graph,selected,pointers)
    load_changes=merged_load_changes(predictions,result)
    if load_changes:
        repeated,repeated_nodes=measure(native,graph,selected,pointers)
        if repeated!=result or repeated_nodes!=node_values:raise ValueError('Merged clock measurement is not repeatable')
    tree=make_tree(graph,source,selected);_,encoding=choices.requirements(tree)
    if encoding['conflicts']:raise ValueError('Merged clock configuration conflicts')
    metrics=dict(status='unconfigured_clock_tree_measured',sinks=result,max_setup_wire_ns=max(r['setup_wire_ns'] for r in result),
        setup_rise_sink_spread_ns=max(r['delays_by_selector'][1][2] for r in result)-min(r['delays_by_selector'][1][2] for r in result),
        nodes=[dict(node=list(p),values_by_selector=v) for p,v in sorted(node_values.items())],vendor_pnr_executed=False,configuration_verified=False,timing_signoff=False)
    report=dict(**space,rejected_paths=dict(rejected),wall_seconds=time.monotonic()-started,merged_delay_predictions_exact=not load_changes,
        merged_load_remeasured=True,merged_load_changes=load_changes,
        objective='reported_setup_slack_with_conservative_hold_bounds' if slack_model else 'worst_insertion_latency',slack_search=slack_report,
        candidate_costs=[dict(node=graph.nodes[t]['coordinate'],alternatives=[dict(path_ids=list(x[3]),measurement=x[4]) for x in options]) for t,options in costs.items()],
        scope='Minimize measured worst rise/fall insertion latency with compatible paths; no global optimality or full clock-mode claim.')
    return tree,metrics,report
