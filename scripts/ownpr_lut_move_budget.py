"""Conservative input/cell/output coupled budget for one ordinary LUT move.

Negative loss is credited only across adjacent arcs through the moved LUT.
Collateral branches are charged separately; their losses cannot be hidden by
an improvement on an unrelated branch. Four entries are slow rise/fall and
fast rise/fall, using worst combinations when transition correlation is unknown.
"""
import math


def coupled_budget(inputs,outputs,collateral,budgets):
    if not inputs or not outputs:raise ValueError('A coupled LUT move needs input and output arcs')
    totals={'setup':0.,'hold':0.};unknown=[];paths=[];side=[]
    def loss(a,b):
        if len(a)!=4 or len(b)!=4 or not all(math.isfinite(v) for v in list(a)+list(b)):raise ValueError('Invalid measured delay vector')
        return {'setup':max(b[0]-a[0],b[1]-a[1]),'hold':max(a[2]-b[2],a[3]-b[3])}
    def charge(value,kind,nodes,label):
        value=max(0.,value)
        if value<=1e-7:return 0.
        # Each through-pin minimum is a lower bound on the slack of any path
        # crossing BOTH pins. Their maximum is still a valid lower bound on
        # that intersection; the minimum needlessly imports unrelated paths.
        bound=max((budgets.get(n,{}).get(kind,0.) for n in nodes),default=0.)
        if not math.isfinite(bound) or bound<=0:
            unknown.append(dict(path=label,kind=kind,loss_ns=value));return float('inf')
        return value/bound
    for incoming in inputs:
        a=loss(incoming['before'],incoming['after'])
        for outgoing in outputs:
            b=loss(outgoing['before'],outgoing['after']);d={k:a[k]+b[k] for k in totals}
            label=[incoming['pin'],outgoing['sink']]
            c={k:charge(d[k],k,[incoming['old_node'],outgoing['sink']],label) for k in totals}
            for k in totals:totals[k]=max(totals[k],c[k])
            paths.append(dict(input_pin=incoming['pin'],sink=outgoing['sink'],loss_ns=d,charges=c))
    for net,records in collateral.items():
        c={k:0. for k in totals};losses=[]
        for r in records:
            d=loss(r['before'],r['after'])
            cc={k:charge(d[k],k,[r['sink']],[net,r['sink']]) for k in c}
            for k in c:c[k]=max(c[k],cc[k])
            if max(d.values())>1e-7:losses.append(dict(r,loss_ns=d,charges=cc,budget=budgets.get(r['sink'])))
        for k in totals:totals[k]+=c[k]
        side.append(dict(net=net,charges=c,worst_sinks=sorted(losses,key=lambda r:-max(r['charges'].values()))[:3]))
    accepted=not unknown and all(v<=1.+1e-9 for v in totals.values())
    clean=lambda values:{k:v if math.isfinite(v) else None for k,v in values.items()}
    for row in paths+side:
        row['charges']=clean(row['charges'])
        for r in row.get('worst_sinks',[]):r['charges']=clean(r['charges'])
    return dict(accepted=accepted,normalized_loss_totals=clean(totals),
        coupled_paths=paths,collateral_nets=side,unknown_or_zero_budget_losses=unknown,
        scope='Worst transition sums with adjacent-arc credit only; positive collateral charges summed globally. Full STA remains mandatory.')


def combined_budget(moves,dependency_graph):
    """Accumulate group charges along conservative combinational paths."""
    from ownpr_wire_budget import path_total
    owner={};charges={}
    for move in moves:
        name=move['lut']
        if name in charges:raise ValueError('Duplicate moved LUT')
        charges[name]=move['budget']['normalized_loss_totals']
        for net in move['affected_nets']:
            if net in owner:raise ValueError('Move groups share a changed loaded tree')
            owner[net]=name
    relations=dependency_graph.relations(owner)
    edges=sorted({(owner[a],owner[b]) for a,b in relations['edges'] if owner[a]!=owner[b]})
    dependency=dict(edges=edges,global_nets=sorted({owner[n] for n in relations['global_nets']}),scope='Conservative data-flow relation between disjoint measured move groups; SCCs charged together')
    totals={k:path_total(charges,dependency,k) for k in ('setup','hold')}
    return dict(accepted=all(v is not None and v<=1.+1e-9 for v in totals.values()),normalized_loss_totals=totals,dependencies=dependency,
        global_sum_comparison={k:sum(v[k] for v in charges.values()) if all(v[k] is not None for v in charges.values()) else None for k in ('setup','hold')})
