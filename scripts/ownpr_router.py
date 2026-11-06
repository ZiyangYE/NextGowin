"""Independent negotiated-congestion, timing-weighted multi-terminal router.

Uses only the architecture's directed programmable edges. No backend invocation.
The delay heuristic is exploratory until the device timing model is validated.
"""
from collections import defaultdict,Counter
import heapq,itertools,time,math


class RoutingTimeout(TimeoutError):
    def __init__(self,checkpoint):
        super().__init__('Own router wall deadline exceeded')
        self.checkpoint=checkpoint


def site_estimator(goals,scale):
    """Exact weighted Manhattan envelope, cached per grid site per search.

    Many wire nodes share a coordinate. Collapsing equal-coordinate goals and
    memoizing coordinates preserves the original heuristic and search costs.
    A fresh estimator is required after the routed tree changes.
    """
    arrivals={}
    for point,arrival in goals:
        point=tuple(point);arrivals[point]=min(arrivals.get(point,float('inf')),arrival)
    if not arrivals:raise ValueError('An estimator needs at least one target')
    cache={}
    def estimate(point):
        point=tuple(point)
        if point not in cache:
            r,c=point;cache[point]=min(scale*(abs(r-y)+abs(c-x))+arrival for (y,x),arrival in arrivals.items())
        return cache[point]
    return estimate


def connection_search(graph,sink,tree,distances,critical,usage,history,present,
        blocked_nodes,seed_owners,name,hscale,arrival_factor,max_expansions,deadline,edges=None,reverse=None):
    """One directed search. A geometric guide never becomes a committed route."""
    reverse=hasattr(graph,'uphill') if reverse is None else reverse
    edges=edges or (graph.uphill if reverse else graph.downhill)
    goals=[(graph.coordinates[n],arrival_factor*distances[n]) for n in tree] if reverse else [(graph.coordinates[sink],0.)]
    estimate=site_estimator(goals,hscale);heap=[];counter=itertools.count();best={};prev={}
    for node in ([sink] if reverse else sorted(tree)):
        initial=0. if reverse else arrival_factor*distances[node]
        best[node]=initial;heapq.heappush(heap,(initial+estimate(graph.coordinates[node]),next(counter),initial,node))
    expanded=0;found=False;join=None;join_cost=float('inf')
    while heap:
        bound,_,g,node=heapq.heappop(heap)
        if g!=best.get(node):continue
        if reverse and arrival_factor and bound>=join_cost:break
        if node in tree if reverse else node==sink:
            if not reverse or not arrival_factor:found=True;break
            cost=g+arrival_factor*distances[node]
            if cost<join_cost:join=node;join_cost=cost
            continue
        expanded+=1
        if expanded%4096==0:deadline()
        if expanded>max_expansions:break
        for dst,delay,pip in edges(node):
            if dst in blocked_nodes or seed_owners.get(dst,name)!=name:continue
            if not reverse and dst in tree:continue
            resource=node if reverse else dst;congestion=len(usage[resource]-{name})
            step=.01+delay*(1+3*critical)+(1-.8*critical)*(history[resource]+present*congestion)
            new=g+step
            if new>=best.get(dst,float('inf')):continue
            best[dst]=new;prev[dst]=(node,pip,delay)
            heapq.heappush(heap,(new+estimate(graph.coordinates[dst]),next(counter),new,dst))
    limited=False
    if reverse and arrival_factor and join is not None:
        node=join;found=True;limited=expanded>max_expansions
    if not found:return None,expanded,limited
    path=[]
    if reverse:
        while node!=sink:
            after,pip,delay=prev[node];path.append((node,after,pip,delay));node=after
    else:
        node=sink
        while node not in tree:
            before,pip,delay=prev[node];path.append((before,node,pip,delay));node=before
        path.reverse()
    return path,expanded,limited


def measured_guide(graph,path):
    """Requery every guide edge using the timing graph before considering it."""
    result=[]
    for u,v,pip,_ in path:
        if hasattr(graph,'pip_cost'):delay=graph.pip_cost(pip)
        else:
            matches=[d for n,d,p in graph.uphill(v) if n==u and tuple(p)==tuple(pip)]
            if len(matches)!=1:raise ValueError('Guide edge absent or ambiguous in timing graph')
            delay=matches[0]
        if not math.isfinite(delay) or delay<0:raise ValueError('Invalid guide edge timing estimate')
        result.append((u,v,pip,delay))
    return result


def connection_objective(path,distances,critical,usage,history,present,name,arrival_factor):
    return arrival_factor*distances[path[0][0]]+sum(.01+delay*(1+3*critical)+
        (1-.8*critical)*(history[v]+present*len(usage[v]-{name})) for u,v,pip,delay in path)


def source_frontier_audit(nets,downhill,max_nodes=64):
    """Reject provably trapped roots before an expensive negotiated search.

    Reaching the finite exploration limit is inconclusive, never a failure.
    This checks only graph reachability, not global occupancy or timing.
    """
    if max_nodes<1:raise ValueError('Source exploration limit must be positive')
    bounded=complete=0
    for net in nets:
        seen=set(net.get('sources',[net['source']]));pending=list(seen);targets=set(net['sinks']);expanded=0
        while pending and expanded<max_nodes and not targets<=seen:
            node=pending.pop();expanded+=1
            for target,_,_ in downhill(node):
                if target not in seen:seen.add(target);pending.append(target)
        if not targets<=seen and not pending:
            raise ValueError(f'Physical source is trapped in a finite component: {net["name"]}; {len(seen)} nodes, {len(targets-seen)} unreachable sinks')
        if targets<=seen:complete+=1
        else:bounded+=1
    return dict(nets_checked=len(nets),small_components_reaching_all_targets=complete,bounded_inconclusive=bounded,
        max_expanded_per_net=max_nodes,provably_trapped_roots=0,complete_design_connectivity_verified=False,timing_signoff=False)

def connection_priorities(net):
    values={};fallback=net.get('criticality',0.)
    if not isinstance(fallback,(int,float)) or not math.isfinite(fallback) or not 0<=fallback<=1:raise ValueError('Invalid net criticality')
    for key,value in net.get('sink_criticalities',{}).items():
        node=int(key)
        if node not in net['sinks'] or node in values or not isinstance(value,(int,float)) or not math.isfinite(value) or not 0<=value<=1:
            raise ValueError('Invalid sink criticality')
        values[node]=value
    return {sink:values.get(sink,fallback) for sink in net['sinks']}


def route(graph,nets,max_iterations=30,timeout=300,max_expansions=500000,progress=None,blocked_nodes=(),initial_trees=None,warm_checkpoint=None,force_reroute=()):
    start=time.monotonic();history=defaultdict(float);usage=defaultdict(set);trees={};iterations=[];heuristic=getattr(graph,'heuristic_scale',.01)
    blocked_nodes=set(blocked_nodes)
    if any(blocked_nodes & (set(n.get('sources',[n['source']]))|set(n['sinks'])) for n in nets):raise ValueError('Routed net endpoint occupies a reserved resource')
    initial_trees=initial_trees or {};byname={n['name']:n for n in nets};seed_owners={}
    connection_weights={n['name']:connection_priorities(n) for n in nets};force_reroute=set(force_reroute)
    if force_reroute and (warm_checkpoint is None or force_reroute-set(byname)):raise ValueError('Forced reroute requires known warm-checkpoint nets')
    if set(initial_trees)-set(byname):raise ValueError('Seed belongs to an unknown net')
    seed_nets=[]
    for name,record in initial_trees.items():
        net=byname[name];sinks=record.get('retained_sinks',[])
        if not sinks or not set(sinks)<=set(net['sinks']):raise ValueError('Seed does not target current common endpoints')
        seed_nets.append(dict(net,sinks=sinks))
        actual=set(net.get('sources',[net['source']]))
        for r,c,a,b in record['pips']:actual.update((graph.node(r,c,a),graph.node(r,c,b)))
        if actual!=set(record['nodes']) or actual&blocked_nodes:raise ValueError('Seed occupancy is incorrect or reserved')
        for node in actual:
            if node in seed_owners and seed_owners[node]!=name:raise ValueError('Seed branches share a physical resource')
            seed_owners[node]=name
    if initial_trees:validate(graph,seed_nets,dict(nets=initial_trees))
    for net in nets:
        if any(seed_owners.get(n,net['name'])!=net['name'] for n in set(net.get('sources',[net['source']]))|set(net['sinks'])):
            raise ValueError('Endpoint occupies another net seed')
    def seed_state(net):
        sources=set(net.get('sources',[net['source']]));record=initial_trees.get(net['name'])
        if record is None:return set(sources),[],{},dict.fromkeys(sources,0.)
        adj=defaultdict(list);parent={};distances=dict.fromkeys(sources,0.)
        for pip in record['pips']:
            r,c,a,b=pip;u=graph.node(r,c,a);v=graph.node(r,c,b)
            if v in parent or v in sources:raise ValueError('Seed has multiple parents or a root cycle')
            parent[v]=u
            if hasattr(graph,'pip_cost'):cost=graph.pip_cost(pip)
            else:
                rows=graph.uphill(v) if hasattr(graph,'uphill') else graph.downhill(u)
                weights=[delay for node,delay,p in rows if tuple(p)==tuple(pip) and node==(u if hasattr(graph,'uphill') else v)]
                if len(weights)!=1:raise ValueError('Seed edge cost is missing or ambiguous')
                cost=weights[0]
            if not math.isfinite(cost) or cost<0:raise ValueError('Invalid seed timing estimate')
            adj[u].append((v,cost))
        stack=list(sources)
        while stack:
            u=stack.pop()
            for v,cost in adj[u]:
                if v in distances:raise ValueError('Cyclic seed')
                distances[v]=distances[u]+cost;stack.append(v)
        if set(distances)!=set(record['nodes']):raise ValueError('Disconnected seed')
        return set(record['nodes']),list(record['pips']),parent,distances
    sequence=sorted(nets,key=lambda n:(-n.get('criticality',0),-len(n['sinks']),str(n['name'])))
    reroute={n['name'] for n in sequence}
    iteration_offset=0;warm_audit=None
    if warm_checkpoint is not None:
        if initial_trees:raise ValueError('Mutable negotiation and frozen branch reuse are different modes')
        from ownpr_route_checkpoint import negotiation_trees
        trees,warm_audit=negotiation_trees(graph,nets,warm_checkpoint,blocked_nodes)
        iterations=list(warm_checkpoint.get('iterations',[]))
        if any(not isinstance(r.get('iteration'),int) or r['iteration']<0 for r in iterations):raise ValueError('Invalid checkpoint iteration ledger')
        iteration_offset=max((r['iteration']+1 for r in iterations),default=0)
        for name,record in trees.items():
            for node in record['nodes']:usage[node].add(name)
        conflicts={node:names for node,names in usage.items() if len(names)>1}
        reroute={name for names in conflicts.values() for name in names}|{n['name'] for n in nets if n['name'] not in trees or not set(n['sinks'])<=set(trees[n['name']]['nodes'])}
        reroute.update(force_reroute)
        if hasattr(graph,'widen_search'):
            for name in sorted(reroute):graph.widen_search(name)
        if 'history' in warm_checkpoint:
            for node,value in warm_checkpoint['history']:
                if not isinstance(node,int) or not math.isfinite(value) or value<0:raise ValueError('Invalid checkpoint congestion history')
                history[node]=value
            warm_audit['congestion_history']='restored'
        else:
            for node,names in conflicts.items():history[node]=max(1,iteration_offset)*(len(names)-1)
            warm_audit['congestion_history']='rebuilt_from_current_conflicts_not_exact_replay'
        if progress:progress(dict(event='negotiation_resume',**warm_audit))
    active_net=None;active_state=None
    def deadline():
        if time.monotonic()-start>timeout:
            partial=dict(trees)
            if active_net is not None and active_state is not None:
                tree,pips,parent,distances=active_state
                partial[active_net['name']]=dict(nodes=sorted(tree),pips=list(pips),parent=dict(parent),estimated_arrival_cost=dict(distances))
            rows={}
            for name,record in partial.items():
                retained=sorted(set(byname[name]['sinks'])&set(record['nodes']))
                if retained:rows[name]=dict(record,retained_sinks=retained)
            raise RoutingTimeout(dict(status='incomplete_route_checkpoint',nets=rows,wall_seconds=time.monotonic()-start,
                iterations=list(iterations),history=[[n,v] for n,v in history.items() if v],connectivity_verified=False,no_node_conflicts=False,timing_signoff=False))
    for iteration in range(iteration_offset,iteration_offset+max_iterations):
        present=1+iteration*2;failed=[]
        for net in sequence:
            deadline();name=net['name']
            if name not in reroute:continue
            if hasattr(graph,'begin_net'):graph.begin_net(net)
            net_start=time.monotonic()
            if progress:progress(dict(event='net_start',iteration=iteration,net=name,elapsed=time.monotonic()-start))
            if name in trees:
                for node in trees[name]['nodes']:usage[node].discard(name)
            sources=set(net.get('sources',[net['source']]))
            tree,pips,parent,distances=seed_state(net);critical=net.get('criticality',0.);limited_searches=0
            active_net=net;active_state=(tree,pips,parent,distances)
            priorities=connection_weights[name]
            for sink in sorted(net['sinks'],key=lambda s:-priorities[s]):
                deadline()
                if sink in tree:continue
                critical=priorities[sink];hscale=heuristic*(1+3*critical)
                reverse=hasattr(graph,'uphill')
                arrival_factor=getattr(graph,'root_arrival_weight',0.)*critical*(1+3*critical) if reverse else 0.
                arguments=(graph,sink,tree,distances,critical,usage,history,present,blocked_nodes,seed_owners,name)
                path=None;guide_info=None
                if getattr(graph,'guided_corridor',False):
                    guide,gexpanded,_=connection_search(*arguments,getattr(graph,'guide_edge_delay',1.)*(1+3*critical),0.,min(10000,max_expansions),deadline,edges=graph.geometric_uphill)
                    guide_info=dict(guide_expanded=gexpanded,guide_found=guide is not None,full_search_fallback=True)
                    if guide is not None:
                        incumbent=measured_guide(graph,guide)
                        graph.set_corridor(guide)
                        try:path,expanded,limited=connection_search(*arguments,hscale,arrival_factor,min(10000,max_expansions),deadline)
                        finally:graph.clear_corridor()
                        candidate_cost=connection_objective(incumbent,distances,critical,usage,history,present,name,arrival_factor)
                        refined_cost=connection_objective(path,distances,critical,usage,history,present,name,arrival_factor) if path is not None else None
                        retain=path is None or candidate_cost<refined_cost
                        if retain:path=incumbent
                        limited=limited or expanded>min(10000,max_expansions)
                        guide_info.update(corridor_expanded=expanded,full_search_fallback=False,measured_guide_retained=retain,
                            measured_guide_cost=candidate_cost,refined_cost=refined_cost)
                if path is None:path,expanded,limited=connection_search(*arguments,hscale,arrival_factor,max_expansions,deadline)
                if path is None and reverse and expanded>max_expansions:
                    reverse_expanded=expanded
                    path,expanded,limited=connection_search(*arguments,hscale,arrival_factor,max_expansions,deadline,reverse=False)
                    if progress:progress(dict(event='source_direction_fallback',net=name,sink=sink,reverse_expanded=reverse_expanded,forward_expanded=expanded,connected=path is not None))
                    limited=True  # A reverse search limit was reached even if the forward search finishes.
                if limited:limited_searches+=1
                if path is None:
                    reason='expansion_limit' if expanded>max_expansions else ('unreachable_in_search_region' if getattr(graph,'search_region_active',False) else 'unreachable_in_graph')
                    failed.append(dict(net=name,sink=sink,expanded=expanded,reason=reason));break
                for before,node,pip,delay in path:
                    tree.add(node);pips.append(pip);parent[node]=before;distances[node]=distances[before]+delay
                if progress:progress(dict(event='sink_done',iteration=iteration,net=name,sink=sink,expanded=expanded,tree_nodes=len(tree),elapsed=time.monotonic()-start,guide=guide_info))
            trees[name]=dict(nodes=sorted(tree),pips=pips,parent=parent,estimated_arrival_cost=distances,
                cost_units=getattr(graph,'delay_units','uncalibrated'),budget_limited_searches=limited_searches)
            for node in tree:usage[node].add(name)
            if progress:progress(dict(event='net_done',iteration=iteration,net=name,seconds=time.monotonic()-net_start,pips=len(pips),failed=[r for r in failed if r['net']==name]))
        conflicts={node:sorted(names,key=str) for node,names in usage.items() if len(names)>1}
        iterations.append(dict(iteration=iteration,overused_nodes=len(conflicts),unrouted_sinks=len(failed)))
        if progress:progress(dict(event='iteration_done',**iterations[-1]))
        if not conflicts and not failed:
            return dict(status='routed',nets=trees,iterations=iterations,wall_seconds=time.monotonic()-start,
                timing_signoff=False,scope='Physical directed-graph connectivity; estimated timing costs only.')
        for node,names in conflicts.items():history[node]+=len(names)-1
        reroute={name for names in conflicts.values() for name in names}|{r['net'] for r in failed}
        if hasattr(graph,'widen_search'):
            # Congestion can require a detour outside a reachable but crowded
            # box, even when every sink was connected in the previous pass.
            for name in sorted(reroute):graph.widen_search(name)
        # A graph-disconnected endpoint cannot be repaired by changing only
        # congestion prices. Return evidence for placement/architecture repair.
        if any(r['reason']=='unreachable_in_graph' for r in failed):break
    return dict(status='unrouted',nets=trees,iterations=iterations,conflicts=conflicts,failed=failed,
        history=[[n,v] for n,v in history.items() if v],wall_seconds=time.monotonic()-start,timing_signoff=False)

def validate(graph,nets,result):
    """Rebuild connectivity from actual PIPs, independent of stored parent trees."""
    occupied={};count=0
    for net in nets:
        name=net['name'];record=result['nets'][name];adj=defaultdict(set);sources=set(net.get('sources',[net['source']]));used=set(sources)
        if len(sources)>1:
            # Multiple physical tie sources are allowed only when explicitly
            # marked constant; arbitrary multi-driver signal nets are invalid.
            if net.get('constant_value') not in (0,1):raise ValueError('Unproved multiple physical drivers')
            expected='VCC' if net['constant_value'] else 'VSS'
            if len(net['sources'])!=len(net.get('source_sites',[])) or not all(graph.node(r,c,expected)==s for s,(r,c) in zip(net['sources'],net['source_sites'])):
                raise ValueError('Constant driver does not match native tie node')
        for pip in record['pips']:
            if not graph.check_pip(pip):raise ValueError('Nonexistent programmable edge '+str(pip))
            r,c,src,dst=pip;a=graph.node(r,c,src);b=graph.node(r,c,dst);adj[a].add(b);used.update((a,b));count+=1
        reached=set(sources);stack=list(reached)
        while stack:
            for b in adj[stack.pop()]:
                if b not in reached:reached.add(b);stack.append(b)
        if not set(net['sinks'])<=reached:raise ValueError('Disconnected sink for '+str(name))
        if used!=reached:raise ValueError('Disconnected route fragment')
        for node in used:
            if node in occupied and occupied[node]!=name:raise ValueError('Physical node owned by two nets')
            occupied[node]=name
    return dict(connectivity_verified=True,no_node_conflicts=True,pips_checked=count,nets_checked=len(nets),timing_signoff=False)
