"""Select reusable complete trees and safe branches of changed ordinary nets."""
from ownpr_router import validate


def signature(net):
    return (net['source'],tuple(sorted(net.get('sources',[net['source']]))),tuple(sorted(net['sinks'])),
        net.get('constant_value'),tuple(sorted(map(tuple,net.get('source_sites',[])))))


def mutable_fallback(graph,nets,frozen,partial,reserved=()):
    """Release freezes while retaining verified trees as negotiable occupancy."""
    from ownpr_route_checkpoint import negotiation_trees
    if partial.get('status') not in ('unrouted','incomplete_route_checkpoint'):raise ValueError('Expected incomplete incremental routing')
    byname={n['name']:n for n in nets}
    if set(frozen)&set(partial['nets']):raise ValueError('Frozen and changed route identities overlap')
    merged={**frozen,**partial['nets']}
    if set(merged)-set(byname):raise ValueError('Fallback includes an unknown current net')
    records={}
    for name,row in merged.items():
        retained=sorted(set(byname[name]['sinks'])&set(row['nodes']))
        if name in frozen and set(retained)!=set(byname[name]['sinks']):raise ValueError('Frozen route is incomplete')
        if retained:records[name]=dict(row,retained_sinks=retained)
    checkpoint=dict(status='incomplete_route_checkpoint',nets=records,iterations=list(partial.get('iterations',[])),
        connectivity_verified=False,no_node_conflicts=False,timing_signoff=False)
    if 'history' in partial:checkpoint['history']=partial['history']
    accepted,audit=negotiation_trees(graph,nets,checkpoint,reserved)
    if audit['rejected']:raise ValueError('Incremental fallback contains reserved resources')
    checkpoint['nets']=accepted
    audit.update(previously_frozen_nets=len(frozen),changed_partial_nets=len(partial['nets']),
        mode='Mutable negotiation retains unaffected trees and reroutes conflicts or missing endpoints')
    return checkpoint,audit


def partition(graph,old_nets,old_routes,new_nets,reserved=()):
    old={n['name']:n for n in old_nets};new={n['name']:n for n in new_nets}
    if len(old)!=len(old_nets) or len(new)!=len(new_nets):raise ValueError('Duplicate net name in incremental routing')
    if old_routes['status']!='routed' or set(old_routes['nets'])!=set(old):raise ValueError('Incomplete source routing for incremental reuse')
    validate(graph,old_nets,old_routes)
    actual={}
    for name,n in old.items():
        nodes=set(n.get('sources',[n['source']]))
        for r,c,a,b in old_routes['nets'][name]['pips']:nodes.update((graph.node(r,c,a),graph.node(r,c,b)))
        if nodes!=set(old_routes['nets'][name]['nodes']):raise ValueError('Stored route occupancy differs from actual PIPs')
        actual[name]=nodes
    frozen={name for name in new if name in old and signature(new[name])==signature(old[name])}
    endpoints={}
    for name,n in new.items():
        for node in set(n.get('sources',[n['source']]))|set(n['sinks']):
            if node in endpoints and endpoints[node]!=name:raise ValueError('New nets share a physical endpoint')
            endpoints[node]=name
    released=[];reserved=set(reserved)
    for name in sorted(frozen):
        if actual[name]&reserved or any(n in endpoints and endpoints[n]!=name for n in actual[name]):released.append(name)
    frozen.difference_update(released)
    blocked=reserved|{node for name in frozen for node in actual[name]}
    return [n for n in new_nets if n['name'] not in frozen],{n:old_routes['nets'][n] for n in sorted(frozen)},blocked,dict(
        reused_nets=len(frozen),rerouted_nets=len(new)-len(frozen),removed_nets=sorted(set(old)-set(new)),
        released_for_endpoint_or_reserved_overlap=released,source_trees_validated=True,
        scope='Exact source/sink reuse with frozen physical PIPs; full merged connectivity, configuration and STA remain required.')


def retained_branches(graph,old_nets,old_routes,changed,new_nets,blocked=()):
    """Keep source-to-common-sink paths; changed sources/constants are excluded."""
    old={n['name']:n for n in old_nets};endpoints={};blocked=set(blocked);seeds={};records=[]
    for net in new_nets:
        for node in set(net.get('sources',[net['source']]))|set(net['sinks']):
            if node in endpoints and endpoints[node]!=net['name']:raise ValueError('New nets share a physical endpoint')
            endpoints[node]=net['name']
    for net in changed:
        name=net['name'];prior=old.get(name)
        if prior is None or prior.get('constant_value') is not None or net.get('constant_value') is not None or net.get('clock'):
            continue
        if set(prior.get('sources',[prior['source']]))!={net['source']} or net.get('sources',[net['source']])!=[net['source']]:continue
        common=set(prior['sinks'])&set(net['sinks'])
        if not common:continue
        record=old_routes['nets'][name];incoming={}
        for pip in record['pips']:
            r,c,a,b=pip;u=graph.node(r,c,a);v=graph.node(r,c,b)
            if v in incoming or v==net['source']:raise ValueError('Source route has multiple parents or a rooted cycle')
            incoming[v]=(u,tuple(pip))
        keep=set();nodes={net['source']};retained=[];released=[]
        for sink in sorted(common):
            path=[];seen={sink};node=sink
            while node!=net['source']:
                if node not in incoming:raise ValueError('Incomplete source branch')
                parent,pip=incoming[node];path.append(pip)
                if parent in seen:raise ValueError('Cyclic source branch')
                seen.add(parent);node=parent
            if seen&blocked or any(p in endpoints and endpoints[p]!=name for p in seen):
                released.append(sink);continue
            retained.append(sink);keep.update(path);nodes.update(seen)
        if retained:
            seeds[name]=dict(nodes=sorted(nodes),pips=[p for p in record['pips'] if tuple(p) in keep],retained_sinks=retained)
            records.append(dict(net=name,retained_sinks=retained,retained_pips=len(keep),released_for_overlap=released,
                removed_sinks=sorted(set(prior['sinks'])-set(net['sinks'])),new_sinks=sorted(set(net['sinks'])-set(prior['sinks']))))
    return seeds,dict(seeded_nets=len(seeds),retained_sinks=sum(len(r['retained_sinks']) for r in records),
        retained_pips=sum(r['retained_pips'] for r in records),records=records,
        scope='Fixed-source ordinary nets only; physical common paths are retained, their loaded delays still require measurement')


def audit_retention(graph,seeds,result):
    checked=0
    for name,seed in seeds.items():
        pips={tuple(p) for p in result['nets'][name]['pips']}
        expected={tuple(p) for p in seed['pips']}
        if not expected<=pips:raise ValueError('Retained branch PIPs changed')
        old_in={graph.node(r,c,b):tuple((r,c,a,b)) for r,c,a,b in seed['pips']};new_in={}
        for p in result['nets'][name]['pips']:
            r,c,a,b=p;new_in.setdefault(graph.node(r,c,b),[]).append(tuple(p))
        for node in seed['nodes']:
            if new_in.get(node,[])!=([old_in[node]] if node in old_in else []):raise ValueError('Retained branch parent changed or became ambiguous')
        checked+=len(expected)
    return dict(seed_nets=len(seeds),preserved_pips=checked,physical_branches_preserved=True,
        timing_preserved=False,scope='PIP preservation only; full-tree timing and final STA remain required')
