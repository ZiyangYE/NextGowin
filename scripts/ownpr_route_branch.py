"""Exact receiver-subtree enumeration and pruning on directed routed trees."""
from collections import defaultdict,deque


def tree(graph,net,record):
    if net.get('clock') or 'constant_value' in net or net.get('sources',[net['source']])!=[net['source']]:raise ValueError('Only ordinary single-driver trees supported')
    incoming={};children=defaultdict(list);used={net['source']}
    for pip in record['pips']:
        r,c,a,b=pip;u,v=graph.node(r,c,a),graph.node(r,c,b)
        if not graph.check_pip(pip) or v in incoming or v==net['source']:raise ValueError('Invalid directed branch tree')
        incoming[v]=(u,pip);children[u].append(v);used.update((u,v))
    order=[];depth={net['source']:0};q=deque([net['source']])
    while q:
        n=q.popleft();order.append(n)
        for d in children[n]:
            if d in depth:raise ValueError('Cyclic branch tree')
            depth[d]=depth[n]+1;q.append(d)
    if set(order)!=used or used!=set(record['nodes']) or not set(net['sinks'])<=used:raise ValueError('Disconnected or incomplete branch tree')
    descendants={n:({n} if n in set(net['sinks']) else set()) for n in order}
    for n in reversed(order):
        for d in children[n]:descendants[n].update(descendants[d])
    return incoming,children,depth,descendants


def branches(graph,net,record,min_sinks=2,max_sinks=32,max_depth=12):
    incoming,children,depth,descendants=tree(graph,net,record)
    return [dict(anchor=n,depth=depth[n],sinks=sorted(s),incoming_pip=incoming[n][1]) for n,s in descendants.items()
            if n!=net['source'] and min_sinks<=len(s)<=min(max_sinks,len(net['sinks'])-1) and depth[n]<=max_depth and len(children[n])>=2]


def prune(graph,net,record,remaining):
    incoming,_,_,_=tree(graph,net,record);remaining=set(remaining)
    if not remaining or not remaining<=set(net['sinks']):raise ValueError('Invalid remaining receiver set')
    keep={net['source']}
    for sink in remaining:
        node=sink
        while node not in keep:
            keep.add(node);node=incoming[node][0]
    pips=[p for p in record['pips'] if graph.node(p[0],p[1],p[3]) in keep]
    result=dict(nodes=sorted(keep),pips=pips)
    tree(graph,dict(net,sinks=sorted(remaining)),result)
    return result
