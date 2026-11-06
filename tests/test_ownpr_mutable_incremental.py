import sys
from pathlib import Path
import pytest
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from ownpr_incremental_routes import mutable_fallback
from ownpr_router import route,validate
from test_ownpr_branch_reuse import Graph


def test_failed_seed_batch_preserves_unrelated_completed_tree():
    from ownpr_incremental_routes import retained_branches
    from test_ownpr_branch_reuse import source
    graph=Graph();del graph.edges[7,5];del graph.edges[5,8]
    graph.coordinates=[(0,0)]*13
    graph.edges.update({(9,10):3.,(10,12):3.,(9,11):.1,(11,12):.1})
    old,prior=source();a=dict(old,sinks=[3,6]);b=dict(name='b',source=7,sinks=[8],criticality=.5)
    c=dict(name='c',source=9,sinks=[12],criticality=.2);nets=[a,b,c]
    seeds,_=retained_branches(graph,[old],prior,nets,nets)
    seeds['c']=dict(nodes=[9,10,12],pips=[[0,0,'9','10'],[0,0,'10','12']],retained_sinks=[12])
    partial=route(graph,nets,initial_trees=seeds)
    assert partial['status']=='unrouted' and partial['failed'][0]['net']=='b'
    cp,audit=mutable_fallback(graph,nets,{},partial)
    events=[];result=route(graph,nets,warm_checkpoint=cp,progress=events.append)
    assert result['status']=='routed' and validate(graph,nets,result)['no_node_conflicts']
    assert result['nets']['c']['pips']==partial['nets']['c']['pips']
    assert not any(e.get('event')=='net_start' and e.get('net')=='c' for e in events)
    assert 1 not in result['nets']['a']['nodes'] and audit['previously_frozen_nets']==0

def test_blocked_changed_net_can_displace_frozen_tree_without_global_ripup():
    graph=Graph();del graph.edges[7,5];del graph.edges[5,8]
    a=dict(name='a',source=0,sinks=[3],criticality=1.);b=dict(name='b',source=7,sinks=[8],criticality=.5)
    frozen={'a':dict(nodes=[0,1,3],pips=[[0,0,'0','1'],[0,0,'1','3']])}
    partial=dict(status='unrouted',nets={'b':dict(nodes=[7],pips=[])},iterations=[])
    cp,audit=mutable_fallback(graph,[a,b],frozen,partial)
    events=[];result=route(graph,[a,b],warm_checkpoint=cp,progress=events.append)
    assert result['status']=='routed' and validate(graph,[a,b],result)['no_node_conflicts']
    assert events[1]['event']=='net_start' and events[1]['net']=='b'
    assert audit['previously_frozen_nets']==1 and not audit['branches_frozen']
    assert 1 not in result['nets']['a']['nodes']

def test_fallback_rejects_overlapping_identities_and_reserved_resources():
    graph=Graph();a=dict(name='a',source=0,sinks=[3]);frozen={'a':dict(nodes=[0,1,3],pips=[[0,0,'0','1'],[0,0,'1','3']])}
    with pytest.raises(ValueError,match='overlap'):mutable_fallback(graph,[a],frozen,dict(status='unrouted',nets=frozen))
    with pytest.raises(ValueError,match='reserved'):mutable_fallback(graph,[a],frozen,dict(status='unrouted',nets={}),reserved={1})
    corrupt={'a':dict(nodes=[0,1,3,9],pips=[[0,0,'0','1'],[0,0,'1','3']])}
    with pytest.raises(ValueError,match='occupancy'):mutable_fallback(graph,[a],corrupt,dict(status='unrouted',nets={}))
