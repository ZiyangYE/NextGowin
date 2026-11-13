import sys
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from ownpr_router import route,validate,RoutingTimeout
from test_ownpr_branch_reuse import Graph


class Guided(Graph):
    def __init__(self,empty=False):
        super().__init__();self.guided_corridor=True;self.active=False;self.empty=empty;self.guide=None
    def geometric_uphill(self,n):
        return [(a,1.,p) for a,d,p in super().uphill(n)]
    def set_corridor(self,path):self.active=True;self.guide=path
    def clear_corridor(self):self.active=False
    def uphill(self,n):
        if self.active and self.empty:return []
        return super().uphill(n)


def test_native_delay_selects_route_independently_of_geometric_guide():
    graph=Guided();net=dict(name='a',source=0,sinks=[3],criticality=1.)
    result=route(graph,[net])
    assert [edge[1] for edge in graph.guide]==[1,3]
    assert result['nets']['a']['parent'][3]==4
    assert result['nets']['a']['estimated_arrival_cost'][3]==pytest.approx(.2)
    assert not graph.active and validate(graph,[net],result)['connectivity_verified']


def test_empty_corridor_retains_measured_guide_and_reserved_resources_remain_blocked():
    graph=Guided(empty=True);events=[];net=dict(name='a',source=0,sinks=[3],criticality=1.)
    result=route(graph,[net],blocked_nodes={4},progress=events.append)
    assert result['nets']['a']['parent'][3]==1 and 4 not in result['nets']['a']['nodes']
    guide=next(e for e in events if e['event']=='sink_done')['guide']
    assert guide['measured_guide_retained'] and not guide['full_search_fallback']
    assert result['nets']['a']['estimated_arrival_cost'][3]==pytest.approx(6.)
    assert not graph.active and validate(graph,[net],result)['no_node_conflicts']


def test_corridor_is_cleared_on_timeout():
    graph=Guided();net=dict(name='a',source=0,sinks=[3])
    def timeout(n):raise RoutingTimeout(dict(status='incomplete_route_checkpoint'))
    graph.uphill=timeout
    with pytest.raises(RoutingTimeout):route(graph,[net])
    assert not graph.active


def test_missing_geometric_guide_uses_full_timing_search():
    graph=Guided();graph.geometric_uphill=lambda n:[];events=[]
    net=dict(name='a',source=0,sinks=[3],criticality=1.)
    result=route(graph,[net],progress=events.append)
    assert result['nets']['a']['parent'][3]==4
    assert next(e for e in events if e['event']=='sink_done')['guide']['full_search_fallback']
    assert graph.guide is None and validate(graph,[net],result)['connectivity_verified']


def test_coarse_edge_units_allow_guide_to_detour_around_congestion():
    from collections import defaultdict
    from ownpr_router import connection_search
    graph=Graph();del graph.edges[0,4];graph.edges[0,5]=.1;graph.edges[5,4]=.1
    usage=defaultdict(set,{1:{'busy'}});history=defaultdict(float)
    def search(scale):
        edges=lambda n:[(a,scale,p) for a,_,p in graph.uphill(n)]
        return connection_search(graph,3,{0},{0:0.},1.,usage,history,2,set(),{},'a',0.,0.,100,lambda:None,edges=edges)[0]
    assert [v for _,v,_,_ in search(1.)]==[1,3]
    assert [v for _,v,_,_ in search(.05)]==[5,4,3]


def test_finite_dead_source_is_rejected_but_search_limit_is_inconclusive():
    from ownpr_router import source_frontier_audit
    n=dict(name='hard_output',source=0,sinks=[10])
    dead=lambda u:[(1,1.,(0,0,'0','1'))] if u==0 else []
    with pytest.raises(ValueError,match='trapped in a finite component'):source_frontier_audit([n],dead)
    chain=lambda u:[(u+1,1.,(0,0,str(u),str(u+1)))] if u<10 else []
    result=source_frontier_audit([n],chain,max_nodes=2)
    assert result['bounded_inconclusive']==1 and not result['complete_design_connectivity_verified']
    assert source_frontier_audit([n],chain)['small_components_reaching_all_targets']==1
