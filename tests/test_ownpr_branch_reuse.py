import copy,json,sys
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from ownpr_router import route,validate
from ownpr_incremental_routes import retained_branches,audit_retention


class Graph:
    def __init__(self):
        self.coordinates=[(0,0)]*10;self.heuristic_scale=0.;self.root_arrival_weight=1.
        self.edges={(0,1):3.,(1,2):1.,(1,3):3.,(0,4):.1,(4,3):.1,(4,6):.1,(7,1):.1,(1,8):.1,(7,5):.2,(5,8):.2}
    def node(self,r,c,w):return int(w)
    def check_pip(self,p):return tuple(map(int,p[2:])) in self.edges
    def uphill(self,n):return [(a,d,(0,0,str(a),str(b))) for (a,b),d in sorted(self.edges.items()) if b==n]


def source():
    net=dict(name='a',source=0,sinks=[2,3],criticality=1.)
    record=dict(nodes=[0,1,2,3],pips=[[0,0,'0','1'],[0,0,'1','2'],[0,0,'1','3']])
    return net,dict(status='routed',nets={'a':record})


def test_preserves_common_path_while_reconnecting_changed_sink():
    graph=Graph();old,routes=source();before=copy.deepcopy(routes);new=dict(old,sinks=[3,6])
    seeds,info=retained_branches(graph,[old],routes,[new],[new])
    assert seeds['a']['nodes']==[0,1,3] and info['retained_pips']==2
    assert info['records'][0]['removed_sinks']==[2] and info['records'][0]['new_sinks']==[6]
    result=route(graph,[new],initial_trees=seeds)
    assert result['status']=='routed' and validate(graph,[new],result)['connectivity_verified']
    assert audit_retention(graph,seeds,result)['preserved_pips']==2 and routes==before
    assert result['nets']['a']['parent'][3]==1
    plain=route(graph,[new]);assert plain['nets']['a']['parent'][3]==4


def test_other_net_cannot_consume_a_seed_before_its_owner_is_routed():
    graph=Graph();old,prior=source();a=dict(old,sinks=[3,6],criticality=.5);b=dict(name='b',source=7,sinks=[8],criticality=1.)
    seeds,_=retained_branches(graph,[old],prior,[a,b],[a,b])
    result=route(graph,[a,b],initial_trees=seeds)
    assert result['status']=='routed' and 1 not in result['nets']['b']['nodes']
    assert validate(graph,[a,b],result)['no_node_conflicts']


def test_reserved_or_other_new_endpoint_releases_common_path():
    graph=Graph();old,prior=source();new=dict(old,sinks=[3,6])
    assert not retained_branches(graph,[old],prior,[new],[new],blocked={1})[0]
    other=dict(name='b',source=7,sinks=[1])
    assert not retained_branches(graph,[old],prior,[new,other],[new,other])[0]
    assert not retained_branches(graph,[old],prior,[dict(new,source=9)],[dict(new,source=9)])[0]
    assert not retained_branches(graph,[dict(old,constant_value=0)],prior,[new],[new])[0]


def test_retention_audit_rejects_added_second_parent_even_when_old_pips_remain():
    graph=Graph();old,prior=source();new=dict(old,sinks=[3,6]);seeds,_=retained_branches(graph,[old],prior,[new],[new])
    result=route(graph,[new],initial_trees=seeds);result['nets']['a']['pips'].append([0,0,'4','3'])
    with pytest.raises(ValueError,match='parent'):audit_retention(graph,seeds,result)


def test_seed_failure_can_be_repaired_by_unseeded_routing():
    graph=Graph();del graph.edges[7,5];del graph.edges[5,8]
    old,prior=source();a=dict(old,sinks=[3,6]);b=dict(name='b',source=7,sinks=[8],criticality=.5)
    seeds,_=retained_branches(graph,[old],prior,[a,b],[a,b])
    assert route(graph,[a,b],initial_trees=seeds,max_iterations=2)['status']=='unrouted'
    fallback=route(graph,[a,b]);assert fallback['status']=='routed'
    assert validate(graph,[a,b],fallback)['no_node_conflicts']


def test_inconsistent_seed_occupancy_is_rejected():
    graph=Graph();old,prior=source();new=dict(old,sinks=[3,6]);seeds,_=retained_branches(graph,[old],prior,[new],[new])
    seeds['a']['nodes'].append(9)
    with pytest.raises(ValueError,match='occupancy'):route(graph,[new],initial_trees=seeds)


