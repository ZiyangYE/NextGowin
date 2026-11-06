"""Delay-informed search graph, followed by mandatory whole-tree evaluation.

Native wire delay depends on the routed load. The unloaded queries below are
search estimates, never signoff delays. Intrinsic entry-position uncertainty
is bounded by enumerating simple paths inside each tiny alias class.
"""
from functools import lru_cache
import math
from ownpr_device import RoutingGraph


class TimingRoutingGraph(RoutingGraph):
    def __init__(self,device,rrg,native):
        super().__init__(device,rrg);self.native=native
        self.heuristic_scale=.001;self.root_arrival_weight=1.
        self.delay_units='ns_search_estimate_requires_whole_tree_recalculation'
        self.margins={};self.bounds=None;self.search_region_active=True
        self.cost_indices={};self.cost_arrays={};self.cost_hits=0;self.cost_misses=0
        self.guided_corridor=False;self.corridor=None;self.guide_edge_delay=1.

    def begin_net(self,net):
        points=[self[n] for n in net.get('sources',[net['source']])+net['sinks']];margin=self.margins.get(net['name'],4)
        self.bounds=(max(0,min(r for r,c in points)-margin),min(self.device.rows-1,max(r for r,c in points)+margin),
            max(0,min(c for r,c in points)-margin),min(self.cols-1,max(c for r,c in points)+margin))
        self.search_region_active=self.bounds!=(0,self.device.rows-1,0,self.cols-1)

    def widen_search(self,name):self.margins[name]=min(256,2*self.margins.get(name,4))

    def in_region(self,pip):
        if self.corridor is not None and tuple(pip[:2]) not in self.corridor:return False
        if self.bounds is None:return True
        r,c=pip[:2];a,b,d,e=self.bounds;return a<=r<=b and d<=c<=e

    def geometric_uphill(self,node):
        # The guide uses the same native-derived directed graph and net region,
        # but does not query native delay for the millions of discarded edges.
        return tuple((node,self.guide_edge_delay,pip) for node,_,pip in super().uphill(node) if self.in_region(pip))

    def set_corridor(self,path,margin=1):
        points={tuple(pip[:2]) for _,_,pip,_ in path}
        points.update(self[n] for u,v,_,_ in path for n in (u,v))
        self.corridor={(r+dy,c+dx) for r,c in points for dy in range(-margin,margin+1) for dx in range(-margin,margin+1)}

    def clear_corridor(self):self.corridor=None

    def physical(self,raw):
        r,c=self[raw];return r+1,c+1,int(raw)%self.n

    @lru_cache(maxsize=300000)
    def unloaded(self,source,destination):
        with self.native.parent_tree({source:None,destination:source}):values=self.native.delay(destination,1)
        cost=max(values[2:4])
        if cost<0:raise ValueError('Negative native wire delay cannot be used as a search cost')
        return cost

    @lru_cache(maxsize=200000)
    def intrinsic_entry(self,point):
        r,c,i=point;owner=self.node(r-1,c-1,self.device.names[i]);key=self.np.uint32(owner)
        a=int(self.np.searchsorted(self.roots,key,'left'));b=int(self.np.searchsorted(self.roots,key,'right'))
        members={self.physical(int(raw)) for raw in self.members[a:b]}
        if len(members)==1:return 0.
        if len(members)>3:raise ValueError('Unexpected native ordinary alias class')
        edges={n:[q for q in self.native.fanouts(n) if q in members and q!=n] for n in members}
        maximum=0.
        def walk(node,seen,cost):
            nonlocal maximum
            if node==point:maximum=max(maximum,cost);return
            for target in edges[node]:
                if target not in seen:walk(target,seen|{target},cost+self.unloaded(node,target))
        for start in members:walk(start,{start},0.)
        return maximum

    def pip_cost(self,pip):
        r,c,source,dest=pip;typ=self.device.grid[r][c]
        if tuple(pip) in self.selected_terminal_pips:
            src=(r+1,c+1,self.device.ids[source]);dst=(r+1,c+1,self.device.ids[dest])
            if dst not in self.native.fanouts(src):raise ValueError('Selected FF terminal absent in native graph')
            return self.unloaded(src,dst)+self.intrinsic_entry(src)
        if typ not in self.cost_indices:
            self.cost_indices[typ]={s*self.n+d:i for i,(s,d) in enumerate((s,d) for s,ds in self.template(typ).items() for d in ds)}
        indices=self.cost_indices[typ];source_id=self.device.ids[source];dest_id=self.device.ids[dest]
        index=indices[source_id*self.n+dest_id];site=(r,c)
        if site not in self.cost_arrays:self.cost_arrays[site]=self.np.full(len(indices),self.np.nan,dtype=self.np.float64)
        array=self.cost_arrays[site];cached=float(array[index])
        if not math.isnan(cached):self.cost_hits+=1;return cached
        self.cost_misses+=1;src=(r+1,c+1,source_id);dst=(r+1,c+1,dest_id)
        if dst not in self.native.fanouts(src):raise ValueError('Search PIP missing from initialized physical device')
        cost=self.unloaded(src,dst)+self.intrinsic_entry(src);array[index]=cost;return cost

    def cost_statistics(self):
        return dict(hits=self.cost_hits,misses=self.cost_misses,tiles=len(self.cost_arrays),
            bytes=sum(a.nbytes for a in self.cost_arrays.values()),cache='Exact per-site arrays, allocated only for visited tiles')

    def uphill(self,node):
        return tuple((before,self.pip_cost(pip),pip) for before,_,pip in super().uphill(node) if self.in_region(pip))

    def downhill(self,node):
        return tuple((after,self.pip_cost(pip),pip) for after,_,pip in super().downhill(node) if self.in_region(pip))
