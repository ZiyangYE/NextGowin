"""Enumerate native clock paths without treating missing encoding as disconnection.

Discovery only: these paths must never enter an accepted implementation without
configuration calibration, shared-resource validation and complete final STA.
"""
from collections import defaultdict,deque,Counter
import heapq


def native_paths(graph,source,sinks,blocked=(),max_paths=20000,max_expansions=100000):
    root=graph.by_coordinate[tuple(source)];targets={graph.by_coordinate[tuple(s)] for s in sinks}
    if not targets or max_paths<1 or max_expansions<1:raise ValueError('Empty targets or invalid path budget')
    blocked={tuple(p) for p in blocked}
    if tuple(source) in blocked or any(tuple(s) in blocked for s in sinks):raise ValueError('Clock endpoint overlaps blocked data')
    reverse=defaultdict(set)
    for a,bs in graph.adj.items():
        for b in bs:reverse[b].add(a)
    ancestors=set(targets);todo=list(targets)
    while todo:
        b=todo.pop()
        if b==root:continue
        for a in reverse[b]:
            if a not in ancestors:ancestors.add(a);todo.append(a)
    adj=defaultdict(list);reach={root};todo=[root]
    while todo:
        a=todo.pop()
        for b in sorted(set(graph.adj[a])):
            if b not in ancestors or tuple(graph.nodes[b]['coordinate']) in blocked:continue
            adj[a].append(b)
            if b not in reach:reach.add(b);todo.append(b)
    if not targets<=reach:raise ValueError('Native clock graph cannot reach all sinks')
    degree=Counter(b for bs in adj.values() for b in bs);queue=deque(sorted(n for n in reach if not degree[n]));order=[]
    while queue:
        a=queue.popleft();order.append(a)
        for b in adj[a]:
            degree[b]-=1
            if degree[b]==0:queue.append(b)
    cyclic=len(order)!=len(reach)
    if cyclic:
        # Fair per-sink search uses shortest remaining hop count to prioritize
        # complete short paths rather than expanding irrelevant prefixes.
        if max_paths<len(targets) or max_expansions<len(targets):raise ValueError('Budget cannot cover every clock sink')
        paths={t:[] for t in sorted(targets)};records=[];expansions=0;count=0;complete=True
        for index,target in enumerate(sorted(targets)):
            quota=max_paths//len(targets)+int(index<max_paths%len(targets));budget=max_expansions//len(targets)+int(index<max_expansions%len(targets))
            wanted={target:0};todo=deque([target])
            while todo:
                b=todo.popleft()
                if b==root:continue
                for a in reverse[b]:
                    if a in reach and a not in wanted:wanted[a]=wanted[b]+1;todo.append(a)
            queue=[(1+wanted[root],(root,))];visits=0
            while queue and visits<budget and len(paths[target])<quota:
                _,path=heapq.heappop(queue);visits+=1
                if path[-1]==target:paths[target].append(path);continue
                for b in adj[path[-1]]:
                    if b in wanted and b not in path:heapq.heappush(queue,(len(path)+1+wanted[b],path+(b,)))
            done=not queue;complete=complete and done;expansions+=visits;count+=len(paths[target])
            records.append(dict(target=target,paths=len(paths[target]),expansions=visits,enumeration_complete=done))
        return paths,dict(status='unencoded_native_path_space',ancestor_nodes=len(ancestors),reachable_nodes=len(reach),edges=sum(map(len,adj.values())),
            sinks=len(targets),candidate_paths=count,cyclic_graph=True,enumeration_complete=complete,expansions=expansions,per_sink=records,configuration_verified=False,timing_signoff=False)
    prefixes={root:[(root,)]}
    for a in order:
        for b in adj[a]:
            rows=prefixes.setdefault(b,[])
            if len(rows)+len(prefixes[a])>max_paths:raise ValueError('Native clock prefix budget exceeded')
            rows.extend(p+(b,) for p in prefixes[a])
    paths={t:prefixes[t] for t in sorted(targets)}
    if sum(map(len,paths.values()))>max_paths:raise ValueError('Native clock total path budget exceeded')
    return paths,dict(status='unencoded_native_path_space',ancestor_nodes=len(ancestors),reachable_nodes=len(reach),edges=sum(map(len,adj.values())),
        sinks=len(targets),candidate_paths=sum(map(len,paths.values())),cyclic_graph=False,enumeration_complete=True,configuration_verified=False,timing_signoff=False)
