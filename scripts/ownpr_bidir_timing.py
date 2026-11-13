"""Rise/fall late/early propagation and reverse required-time propagation.

Fixed single-clock boundary constraints are imported separately. This engine
evaluates complete DAGs, including paths absent from old worst-path reports.
Its outputs remain optimization feedback; native complete STA is mandatory.
"""
import collections,math


def topology(graph):
    nodes=graph['nodes'];incoming=collections.defaultdict(list);outgoing=collections.defaultdict(list);degree=dict.fromkeys(nodes,0)
    if len(degree)!=len(nodes):raise ValueError('Duplicate timing vertex')
    for i,e in enumerate(graph['edges']):
        if e['source'] not in degree or e['destination'] not in degree:raise ValueError('Unknown timing vertex')
        if len(e['delay'])!=2 or any(not math.isfinite(v) or v<0 for v in e['delay']):raise ValueError('Invalid timing delay')
        if not e['transitions'] or any(a not in (0,1) or b not in (0,1) for a,b in e['transitions']):raise ValueError('Invalid transition relation')
        incoming[e['destination']].append(i);outgoing[e['source']].append(i);degree[e['destination']]+=1
    queue=collections.deque(n for n in nodes if not degree[n]);order=[]
    while queue:
        n=queue.popleft();order.append(n)
        for i in outgoing[n]:
            d=graph['edges'][i]['destination'];degree[d]-=1
            if not degree[d]:queue.append(d)
    if len(order)!=len(nodes):raise ValueError('Combinational timing cycle')
    return order,incoming,outgoing


def analyze(graph,terminals,early=False):
    order,incoming,outgoing=topology(graph);initial=math.inf if early else -math.inf
    arrival={n:[initial,initial] for n in order};required={n:[-initial,-initial] for n in order}
    choose=min if early else max;restrict=max if early else min
    for n,values in graph['seeds'].items():
        if n not in arrival or len(values)!=2 or any(not math.isfinite(v) for v in values):raise ValueError('Invalid timing seed')
        arrival[n]=list(values)
    for n,values in terminals.items():
        if n not in required or len(values)!=2 or any(not math.isfinite(v) for v in values):raise ValueError('Invalid endpoint requirement')
        required[n]=list(values)
    for n in order:
        for i in incoming[n]:
            e=graph['edges'][i]
            for a,b in e['transitions']:arrival[n][b]=choose(arrival[n][b],arrival[e['source']][a]+e['delay'][b])
    for n in reversed(order):
        for i in outgoing[n]:
            e=graph['edges'][i]
            for a,b in e['transitions']:required[n][a]=restrict(required[n][a],required[e['destination']][b]-e['delay'][b])
    slack={n:[arrival[n][i]-required[n][i] if early else required[n][i]-arrival[n][i] for i in (0,1)] for n in order}
    endpoints={n:min(slack[n]) for n in terminals}
    if not endpoints or any(not math.isfinite(v) for v in endpoints.values()):raise ValueError('Uncovered timing endpoint')
    return dict(arrival=arrival,required=required,slack=slack,endpoints=endpoints,wns=min(endpoints.values()),tns=sum(min(0.,v) for v in endpoints.values()),violations=sum(v<0 for v in endpoints.values()))


def changed_graph(graph,edge_records):
    """Atomic pure construction; source graph remains unchanged on failure."""
    if any(type(i)!=int or not 0<=i<len(graph['edges']) for i in edge_records):raise ValueError('Unknown changed edge')
    candidate=dict(graph,edges=[edge_records.get(i,e) for i,e in enumerate(graph['edges'])]);topology(candidate)
    return candidate
