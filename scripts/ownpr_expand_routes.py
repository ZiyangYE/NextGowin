"""Expand canonical wire resources into directed physical native node trees.

Alias equivalence is sufficient for occupancy, but does not imply zero delay
or bidirectional traversal. Every physical step is checked against fanouts.
"""
from collections import defaultdict,deque
from ownpr_router import validate


def expand(graph,nets,routes,fanouts):
    validate(graph,nets,routes)
    def coordinate(raw):
        r,c=graph[raw];return r+1,c+1,int(raw)%graph.n
    def resource(node):
        r,c,i=node
        if not 1<=r<=graph.device.rows or not 1<=c<=graph.cols or i not in graph.device.names:
            return None
        return graph.node(r-1,c-1,graph.device.names[i])
    def unique_point(node):
        key=graph.np.uint32(node);a=int(graph.np.searchsorted(graph.roots,key,'left'));b=int(graph.np.searchsorted(graph.roots,key,'right'))
        if b-a!=1:raise ValueError('Logical endpoint requires an explicit physical member')
        return coordinate(int(graph.members[a]))
    def intrinsic_path(start,end):
        if start==end:return [start]
        owner=resource(start)
        if resource(end)!=owner:raise ValueError('Intrinsic endpoints are not the same wire resource')
        seen={start:None};queue=deque([start])
        while queue:
            node=queue.popleft()
            for target in fanouts(node):
                if target not in seen and resource(target)==owner:
                    seen[target]=node;queue.append(target)
                    if target==end:
                        path=[end]
                        while path[-1]!=start:path.append(seen[path[-1]])
                        return path[::-1]
        raise ValueError(f'Canonical alias is not a directed physical connection: {start} -> {end}')
    expanded={};global_owners={};total_intrinsic=0;total_local=0
    for net in nets:
        name=net['name'];sources=net.get('sources',[net['source']]);arrival={s:unique_point(s) for s in sources}
        parents={v:None for v in arrival.values()};pips=routes['nets'][name]['pips'];physical=[]
        for r,c,src,dst in pips:
            source=(r+1,c+1,graph.device.ids[src]);dest=(r+1,c+1,graph.device.ids[dst]);a=resource(source);b=resource(dest)
            if b in arrival:raise ValueError('Multiple entries into one physical wire resource')
            if dest not in fanouts(source):raise ValueError(f'PIP absent in native physical graph: {source} -> {dest}')
            arrival[b]=dest;physical.append((a,source,dest))
        def add(before,node):
            if node in parents and parents[node]!=before:raise ValueError('Incompatible physical parent selections')
            parents[node]=before
        intrinsic=set()
        for owner,source,dest in physical:
            if owner not in arrival:raise ValueError('Disconnected source wire resource')
            chain=intrinsic_path(arrival[owner],source)
            for before,node in zip(chain,chain[1:]):add(before,node);intrinsic.add((before,node))
            add(source,dest)
        sinks=[]
        for sink in net['sinks']:
            point=unique_point(sink);chain=[];seen=set();node=point
            while node is not None:
                if node in seen or node not in parents:raise ValueError('Physical endpoint chain is cyclic or disconnected')
                seen.add(node);chain.append(node);node=parents[node]
            if chain[-1] not in {arrival[s] for s in sources}:raise ValueError('Physical path reaches wrong driver')
            sinks.append(dict(resource=sink,node=point,path_sink_to_source=chain))
        for point in parents:
            if point in global_owners and global_owners[point]!=name:raise ValueError('Physical node shared by different nets')
            global_owners[point]=name
        expanded[name]=dict(parents=[dict(node=n,parent=p) for n,p in sorted(parents.items())],sinks=sinks,
            sources=[arrival[s] for s in sources],clock=net.get('clock',False),intrinsic_edges=len(intrinsic),local_edges=len(physical))
        total_intrinsic+=len(intrinsic);total_local+=len(physical)
    return dict(nets=expanded,status='expanded',physical_nodes=len(global_owners),intrinsic_edges=total_intrinsic,
        local_edges=total_local,timing_signoff=False,scope='Directed physical expansion and connectivity of own ordinary routes only.')
