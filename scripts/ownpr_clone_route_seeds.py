"""Transfer physical branches across renamed nets with identical source nodes.

Cloning may give the old physical output a fresh logical net name. Electrical
reuse depends on physical source and sinks, not that name. This only supplies
legal routing seeds; complete loaded timing and global CEC remain mandatory.
"""
from ownpr_incremental_routes import retained_branches
from ownpr_router import validate


def reuse_by_source(graph,old_nets,old_routes,changed,new_nets,blocked,initial):
    eligible=lambda n:not n.get('clock') and 'constant_value' not in n and n.get('sources',[n['source']])==[n['source']]
    by_source={}
    for net in old_nets:
        if not eligible(net):continue
        if net['source'] in by_source:raise ValueError('Ambiguous original physical driver')
        by_source[net['source']]=net
    seeds=dict(initial);occupied=set(blocked)
    for record in seeds.values():occupied.update(record['nodes'])
    receipts=[]
    for net in sorted(changed,key=lambda n:n['name']):
        if net['name'] in seeds or not eligible(net):continue
        old=by_source.get(net['source'])
        if old is None or old['name']==net['name']:continue
        alias=dict(old,name=net['name']);routes=dict(nets={net['name']:old_routes['nets'][old['name']]})
        reused,stats=retained_branches(graph,[alias],routes,[net],new_nets,occupied)
        if net['name'] not in reused:continue
        record=reused[net['name']]
        validate(graph,[dict(net,sinks=record['retained_sinks'])],dict(nets={net['name']:record}))
        seeds[net['name']]=record;occupied.update(record['nodes']);receipts.append(dict(donor=old['name'],recipient=net['name'],physical_source=net['source'],retained_sinks=record['retained_sinks'],retained_pips=len(record['pips'])))
    return seeds,dict(transfers=receipts,scope='Physical source/sink identity only; no timing or logical-equivalence claim')
