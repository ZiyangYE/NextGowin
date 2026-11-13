"""Remove a terminal branch and reconnect it to retained tree resources.

Search uses device costs; acceptance is left to whole-tree timing evaluation.
Hard ownership restrictions prevent disturbing any other net.
"""
import heapq,itertools,time
from collections import defaultdict


def reconnect(graph,net,record,sink,owners,deadline,max_expansions=20000,max_candidates=90,labels_per_node=3,original_sink=None):
    incoming={};outgoing=defaultdict(list)
    for pip in record['pips']:
        r,c,a,b=pip;u=graph.node(r,c,a);v=graph.node(r,c,b)
        if v in incoming:raise ValueError('Multiple branch parents')
        incoming[v]=(u,pip);outgoing[u].append(v)
    old_sink=sink if original_sink is None else original_sink
    if old_sink not in incoming or outgoing[old_sink]:return [],dict(reason='not_terminal_sink')
    removed=[];discard={old_sink};node=old_sink
    while node in incoming:
        parent,pip=incoming[node];removed.append(pip);node=parent
        if len(outgoing[node])!=1 or node in net['sinks'] or node==net['source']:break
        discard.add(node)
    retained=set(record['nodes'])-discard
    base=[p for p in record['pips'] if p not in removed]
    counter=itertools.count();heap=[(0.,next(counter),sink,(),frozenset({sink}))];settled=defaultdict(int);candidates=[];expanded=0
    graph.begin_net(net)
    while heap and expanded<max_expansions and len(candidates)<max_candidates:
        cost,_,node,tail,visited=heapq.heappop(heap)
        if settled[node]>=labels_per_node:continue
        settled[node]+=1
        if expanded%128==0 and time.monotonic()>deadline:raise TimeoutError('Branch reconnect deadline')
        if node in retained:
            pips=base+list(tail);used={net['source']}
            for r,c,a,b in pips:used.update((graph.node(r,c,a),graph.node(r,c,b)))
            candidates.append(dict(pips=pips,nodes=sorted(used),parent={},estimated_arrival_cost={},cost_units='branch_reconnect_candidate'))
            continue
        expanded+=1
        for before,delay,pip in graph.uphill(node):
            if before in owners and owners[before]!=net['name']:continue
            if before in visited or settled[before]>=labels_per_node:continue
            new=cost+delay+.001
            heapq.heappush(heap,(new,next(counter),before,(pip,)+tail,visited|{before}))
    return candidates,dict(expanded=expanded,candidates=len(candidates),removed_pips=len(removed),retained_nodes=len(retained),search_exhausted=not heap)
