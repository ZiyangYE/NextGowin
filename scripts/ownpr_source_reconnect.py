"""Reconnect a moved ordinary driver to verified downstream route fragments."""
import time
from collections import defaultdict
from ownpr_router import route,validate,RoutingTimeout

def fragments(graph,old,record,new,depth):
    if depth<1:raise ValueError('Source cut depth must be positive')
    if any(n.get('clock') or 'constant_value' in n or n.get('sources',[n['source']])!=[n['source']] for n in (old,new)):raise ValueError('Only ordinary single-driver nets can be reanchored')
    if old['name']!=new['name'] or old['source']==new['source']:raise ValueError('Expected one logical net with a changed physical source')
    validate(graph,[old],dict(nets={old['name']:record}))
    incoming={};actual={old['source']}
    for p in record['pips']:
        r,c,a,b=p;u,v=graph.node(r,c,a),graph.node(r,c,b)
        if v in incoming or v==old['source']:raise ValueError('Old source tree is cyclic or has multiple parents')
        incoming[v]=(u,tuple(p));actual.update((u,v))
    if actual!=set(record['nodes']):raise ValueError('Old tree occupancy is inconsistent')
    common=set(old['sinks'])&set(new['sinks']);keep=set();roots=set();kept_sinks=[];root_sinks=defaultdict(list)
    for sink in sorted(common):
        path=[];seen={sink};node=sink
        while node!=old['source']:
            if node not in incoming:raise ValueError('Old sink lacks a source path')
            parent,pip=incoming[node]
            if parent in seen:raise ValueError('Old source tree contains a cycle')
            path.append((parent,node,pip));seen.add(parent);node=parent
        path.reverse();cut=min(depth,len(path))
        if not cut:raise ValueError('Old sink is the source itself')
        anchor=path[cut-1][1];roots.add(anchor);root_sinks[anchor].append(sink);keep.update(p for _,_,p in path[cut:]);kept_sinks.append(sink)
    if not kept_sinks:raise ValueError('No unchanged receiver branch')
    pips=[p for p in record['pips'] if tuple(p) in keep];nodes=set(roots)
    for r,c,a,b in pips:nodes.update((graph.node(r,c,a),graph.node(r,c,b)))
    if old['source'] in nodes or new['source'] in nodes:raise ValueError('A source overlaps a retained fragment')
    parents={graph.node(r,c,b) for r,c,a,b in pips}
    if roots&parents:raise ValueError('Fragment roots have retained parents')
    return dict(roots=sorted(roots),nodes=sorted(nodes),pips=pips,retained_sinks=kept_sinks,root_sinks=dict(root_sinks),depth=depth)

def reconnect_source(graph,old,record,new,owners,deadline,max_depth=6,seconds_per_depth=12):
    candidates=[];trials=[];seen=set();foreign={n for n,owner in owners.items() if owner!=new['name']}
    for depth in range(1,max_depth+1):
        if time.monotonic()>deadline:break
        fragment=fragments(graph,old,record,new,depth);roots=set(fragment['roots']);nodes=set(fragment['nodes']);key=tuple(fragment['roots'])
        if key in seen:continue
        seen.add(key)
        if nodes&foreign:
            trials.append(dict(depth=depth,status='fragment_occupied',overlap_nodes=len(nodes&foreign)));continue
        # The connector may touch fragment roots, never enter the retained
        # interior and create another parent or a cycle.
        blocked=foreign|(nodes-roots)|{old['source']};anchor=dict(new,sinks=sorted(roots))
        priorities=new.get('sink_criticalities',{})
        anchor['sink_criticalities']={r:max(priorities.get(s,priorities.get(str(s),new.get('criticality',.5))) for s in fragment['root_sinks'][r]) for r in roots}
        anchor['criticality']=max(anchor['sink_criticalities'].values())
        try:
            lead=route(graph,[anchor],max_iterations=2,timeout=max(.01,min(seconds_per_depth,deadline-time.monotonic())),max_expansions=60000,blocked_nodes=blocked)
            if lead['status']!='routed':trials.append(dict(depth=depth,status='no_connector',failed=lead.get('failed')));continue
            prefix=lead['nets'][new['name']];pips=prefix['pips']+fragment['pips'];used=set(prefix['nodes'])|nodes
            seed=dict(pips=pips,nodes=sorted(used),retained_sinks=fragment['retained_sinks'])
            validate(graph,[dict(new,sinks=fragment['retained_sinks'])],dict(nets={new['name']:seed}))
            if set(new['sinks'])!=set(fragment['retained_sinks']):
                completed=route(graph,[new],max_iterations=2,timeout=max(.01,min(seconds_per_depth,deadline-time.monotonic())),max_expansions=60000,blocked_nodes=foreign|{old['source']},initial_trees={new['name']:seed})
                if completed['status']!='routed':trials.append(dict(depth=depth,status='new_sink_failed'));continue
                seed=completed['nets'][new['name']]
            validate(graph,[new],dict(nets={new['name']:seed}))
            parents={}
            for r,c,a,b in seed['pips']:
                u,v=graph.node(r,c,a),graph.node(r,c,b)
                if v in parents or v==new['source']:raise ValueError('Reanchored tree has multiple parents or a root cycle')
                parents[v]=u
            candidates.append(dict(record=seed,depth=depth,retained_pips=len(fragment['pips']),connector_pips=len(prefix['pips'])))
            trials.append(dict(depth=depth,status='connected',retained_pips=len(fragment['pips']),connector_pips=len(prefix['pips'])))
        except RoutingTimeout:trials.append(dict(depth=depth,status='timeout'))
    return candidates,dict(trials=trials,scope='Preserved downstream PIPs with a new physical driver; complete loaded timing must be measured')
