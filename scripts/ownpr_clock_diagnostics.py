"""Bounded simple-path sampling of the native graph, including unknown modes.

Diagnostic paths are not legal clock candidates until their encoding and
physical mode constraints are independently established.
"""
from collections import defaultdict,deque
import heapq


def sample_paths(graph,source,sink,max_paths=512,max_nodes=13,max_states=200000):
    if min(max_paths,max_nodes,max_states)<1:raise ValueError('Clock sampling budgets must be positive')
    root=graph.by_coordinate[tuple(source)];target=graph.by_coordinate[tuple(sink)];reverse=defaultdict(set)
    for a,bs in graph.adj.items():
        for b in bs:reverse[b].add(a)
    distance={target:0};queue=deque([target])
    while queue:
        b=queue.popleft()
        for a in reverse[b]:
            if a not in distance:distance[a]=distance[b]+1;queue.append(a)
    if root not in distance:raise ValueError('Native clock endpoint is unreachable')
    heap=[(distance[root],(root,))];paths=[];states=0
    while heap and len(paths)<max_paths and states<max_states:
        _,path=heapq.heappop(heap);states+=1;node=path[-1]
        if node==target:paths.append(path);continue
        for child in sorted(set(graph.adj[node])):
            if child not in distance or child in path:continue
            if len(path)+1+distance[child]>max_nodes:continue
            next_path=path+(child,);heapq.heappush(heap,(len(path)+distance[child],next_path))
    return paths,dict(paths=len(paths),expanded_states=states,max_paths=max_paths,max_nodes=max_nodes,max_states=max_states,
        pending_states=len(heap),enumeration_complete=not heap,enumeration_scope='simple paths within max_nodes',configuration_checked=False)
