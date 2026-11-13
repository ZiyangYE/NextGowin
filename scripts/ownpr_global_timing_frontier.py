"""Select a bounded synthesis frontier across full endpoint roles and regions.

Reachability is deliberately reported separately from timing benefit. This
proposal scheduler changes neither STA nor acceptance, and is not evidence
that a register, hard block or clock can already be moved.
"""
import math
from collections import defaultdict
from ownpr_bidir_timing import topology
from ownpr_critical_chain_move import locations
from audit_joint_boundary_coverage import role


def select_roots(design,layout,graph,metrics,eligible,limit=40,region_size=16):
    if limit<1 or region_size<1:raise ValueError('Invalid frontier budget')
    cells=design['modules']['top']['cells'];points=locations(layout)
    buckets=defaultdict(list)
    for endpoint,slack in metrics['endpoints'].items():
        name,_,pin=endpoint.rpartition('/');point=points.get(name)
        key=(role(name,pin,cells,layout),tuple(v//region_size for v in point) if point else None)
        buckets[key].append((endpoint,slack))
    keys=sorted(buckets,key=str);index={key:i for i,key in enumerate(keys)}
    masks={node:0 for node in graph['nodes']}
    group_rows=[]
    for key,i in index.items():
        endpoints=buckets[key];wns=min(s for _,s in endpoints);tns=sum(min(0.,s) for _,s in endpoints)
        violations=sum(s<0 for _,s in endpoints)
        # Nonviolating regions retain a positive exploration demand.
        weight=1.+max(0.,-wns)+(-tns/max(1,violations))
        group_rows.append(dict(role=key[0],region=list(key[1]) if key[1] else None,
            endpoints=len(endpoints),wns_ns=wns,tns_ns=tns,violations=violations,demand=weight))
        for endpoint,_ in endpoints:masks[endpoint]|=1<<i
    order,_,outgoing=topology(graph)
    for node in reversed(order):
        for edge_id in outgoing[node]:masks[node]|=masks[graph['edges'][edge_id]['destination']]
    wire=defaultdict(float)
    for edge in graph['edges']:
        if edge['kind']=='wire':wire[edge['destination']]=max(wire[edge['destination']],max(edge['delay']))
    candidates={}
    for name in eligible:
        vertex=name+('/O' if cells[name]['type'].startswith('MUX2_LUT') else '/F');slack=min(metrics['slack'].get(vertex,[math.inf]))
        if vertex not in masks or not masks[vertex] or not math.isfinite(slack):continue
        candidates[name]=dict(mask=masks[vertex],slack=slack,
            incoming_wire_ns=max((wire.get(name+'/'+pin,0.) for pin in cells[name]['connections']),default=0.))
    def members(mask):return [i for i in range(len(keys)) if mask>>i&1]
    reachable=0
    for row in candidates.values():reachable|=row['mask']
    uncovered=reachable;selected=[];details=[]
    while candidates and len(selected)<limit:
        def score(name):
            row=candidates[name];fresh=row['mask']&uncovered
            return (sum(group_rows[i]['demand'] for i in members(fresh)),
                    -row['slack'],row['incoming_wire_ns'])
        name=min(candidates,key=lambda n:(tuple(-v for v in score(n)),n))
        row=candidates.pop(name);fresh=row['mask']&uncovered;uncovered&=~row['mask']
        selected.append(name);details.append(dict(root=name,through_slack_ns=row['slack'],
            incoming_wire_ns=row['incoming_wire_ns'],reachable_groups=members(row['mask']),newly_represented_groups=members(fresh)))
    for i,row in enumerate(group_rows):row.update(reachable_by_eligible_root=bool(reachable>>i&1),represented_by_selected_root=bool((reachable&~uncovered)>>i&1))
    return selected,dict(status='global_endpoint_region_frontier',groups=group_rows,selected=details,
        eligible_roots_considered=len(candidates)+len(selected),selected_roots=len(selected),
        unrepresented_reachable_groups=members(uncovered),
        scope='Full endpoint fanin reachability coverage; not a guarantee of critical-path coverage, transform feasibility, timing benefit or movable-state support.')
