"""A conservative shared budget for loaded wire-delay changes.

Per-net maximum normalized loss accumulates along conservative data-flow
paths, or globally when no dependency graph is supplied. This prevents
spending a path's entire slack independently at each net. Cell/slew effects
still require full STA.
"""
import math
from collections import defaultdict,deque

class WireDependency:
    """Conservative data-flow relation; synchronous FFs stop timing paths.

    Hard/unknown primitives pass all inputs to all outputs, intentionally
    over-approximating combinational reachability. Cycles are condensed and
    charged as a whole, never treated as free timing cuts.
    """
    def __init__(self,design):
        self.edges=defaultdict(set);self.bits=set()
        for cell in design['modules']['top']['cells'].values():
            ins=[b for p,bs in cell['connections'].items() if cell['port_directions'][p] in ('input','inout') for b in bs]
            outs=[b for p,bs in cell['connections'].items() if cell['port_directions'][p] in ('output','inout') for b in bs]
            self.bits.update(ins+outs)
            if cell['type'] in ('DFF','DFFE','DFFR','DFFS','DFFRE','DFFSE'):continue
            for bit in ins:self.edges[bit].update(outs)

    def relations(self,names):
        bybit={};global_nets=[]
        for name in names:
            try:bit=int(name.removeprefix('net_')) if name.startswith('net_') else None
            except ValueError:bit=None
            if bit not in self.bits:global_nets.append(name)
            else:
                if bit in bybit:raise ValueError('Multiple routing names for one dependency bit')
                bybit[bit]=name
        edges=set()
        for start,name in bybit.items():
            seen={start};stack=list(self.edges[start])
            while stack:
                bit=stack.pop()
                if bit in bybit:edges.add((name,bybit[bit]));continue
                if bit in seen:continue
                seen.add(bit);stack.extend(self.edges[bit])
        return dict(edges=sorted(edges),global_nets=sorted(global_nets),scope='All-input/all-output reachability with synchronous FF boundaries; SCCs charged conservatively')

def path_total(charges,dependencies,kind):
    names=set(charges);global_nets=set(dependencies['global_nets'])
    if not global_nets<=names:raise ValueError('Unknown globally charged net')
    edges={n:set() for n in names-global_nets};reverse={n:set() for n in edges}
    for a,b in dependencies['edges']:
        if a not in edges or b not in edges:raise ValueError('Budget dependency references an unknown net')
        edges[a].add(b);reverse[b].add(a)
    if any(charges[n][kind] is None for n in names):return None
    seen=set();finish=[]
    for node in sorted(edges):
        if node in seen:continue
        stack=[(node,False)]
        while stack:
            n,done=stack.pop()
            if done:finish.append(n);continue
            if n in seen:continue
            seen.add(n);stack.append((n,True));stack.extend((v,False) for v in sorted(edges[n],reverse=True) if v not in seen)
    component={};groups=[]
    for node in reversed(finish):
        if node in component:continue
        index=len(groups);group=[];stack=[node];component[node]=index
        while stack:
            n=stack.pop();group.append(n)
            for v in reverse[n]:
                if v not in component:component[v]=index;stack.append(v)
        groups.append(group)
    successors={i:set() for i in range(len(groups))};degree=[0]*len(groups)
    for n,children in edges.items():
        for v in children:
            a,b=component[n],component[v]
            if a!=b and b not in successors[a]:successors[a].add(b);degree[b]+=1
    weights=[sum(charges[n][kind] for n in g) for g in groups];distance=list(weights);queue=deque(i for i,d in enumerate(degree) if d==0)
    while queue:
        i=queue.popleft()
        for j in successors[i]:
            distance[j]=max(distance[j],distance[i]+weights[j]);degree[j]-=1
            if degree[j]==0:queue.append(j)
    return max(distance,default=0.)+sum(charges[n][kind] for n in global_nets)

def ledger(changes,budgets,dependencies=None):
    rows=[];unknown=[];totals={'setup':0.,'hold':0.}
    for name,pair in sorted(changes.items()):
        before,after=pair
        if set(before)!=set(after):raise ValueError('Budget ledger sink contract changed')
        charges={'setup':0.,'hold':0.};sinks=[]
        for sink in before:
            a,b=before[sink],after[sink]
            if len(a)!=4 or len(b)!=4 or any(not math.isfinite(x) or x<0 for x in a+b):raise ValueError('Invalid loaded delay vector')
            losses=dict(setup=max(0.,b[0]-a[0],b[1]-a[1]),hold=max(0.,a[2]-b[2],a[3]-b[3]));bound=budgets.get(sink)
            for kind,loss in losses.items():
                if loss<=1e-7:continue
                value=bound.get(kind) if bound else None
                if value is None or not math.isfinite(value) or value<=0:
                    unknown.append(dict(net=name,sink=sink,kind=kind,loss_ns=loss));charges[kind]=None
                elif charges[kind] is not None:charges[kind]=max(charges[kind],loss/value)
            sinks.append(dict(sink=sink,loss_ns=losses,budget=bound))
        for kind in totals:
            if charges[kind] is None:totals[kind]=None
            elif totals[kind] is not None:totals[kind]+=charges[kind]
        rows.append(dict(net=name,charges=charges,sinks=sinks))
    global_totals=dict(totals)
    if dependencies is not None:totals={k:path_total({r['net']:r['charges'] for r in rows},dependencies,k) for k in totals}
    accepted=not unknown and all(v is not None and v<=1.+1e-9 for v in totals.values())
    return dict(accepted=accepted,normalized_loss_totals=totals,global_sum_comparison=global_totals,dependencies=dependencies,unknown_or_zero_budget_losses=unknown,nets=rows,
        scope='Conservative shared wire-only budget; not a replacement for full setup/hold STA')

def delays(data,name):
    return {r['resource']:[r['delays_by_selector'][1][2],r['delays_by_selector'][1][3],r['delays_by_selector'][2][0],r['delays_by_selector'][2][1]] for r in data['nets'][name]['sinks']}
