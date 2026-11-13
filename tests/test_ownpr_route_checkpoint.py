import copy,sys
from pathlib import Path
import pytest
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
import ownpr_router
from ownpr_router import route,validate,RoutingTimeout
from ownpr_route_checkpoint import safe_seeds
from test_ownpr_branch_reuse import Graph


def test_timeout_retains_only_connected_prefix_and_can_resume(monkeypatch):
    now=[0.];monkeypatch.setattr(ownpr_router.time,'monotonic',lambda:now[0])
    graph=Graph();net=dict(name='a',source=0,sinks=[2,3],criticality=1.)
    def progress(row):
        if row['event']=='sink_done':now[0]=2.
    with pytest.raises(RoutingTimeout) as caught:route(graph,[net],timeout=1,progress=progress)
    cp=caught.value.checkpoint
    assert cp['status']=='incomplete_route_checkpoint' and not cp['connectivity_verified']
    assert cp['nets']['a']['retained_sinks']==[2]
    seeds,audit=safe_seeds(graph,[net],cp)
    assert audit['retained_sinks']==1 and not audit['complete_design_connectivity_verified']
    result=route(graph,[net],timeout=1,initial_trees=seeds)
    assert result['status']=='routed' and validate(graph,[net],result)['connectivity_verified']
    assert set(map(tuple,seeds['a']['pips']))<=set(map(tuple,result['nets']['a']['pips']))


def checkpoint():
    return dict(status='incomplete_route_checkpoint',nets={'a':dict(nodes=[0,1,2],pips=[[0,0,'0','1'],[0,0,'1','2']],retained_sinks=[2])})


def test_checkpoint_cannot_lock_another_net_endpoint_or_reserved_wire():
    graph=Graph();a=dict(name='a',source=0,sinks=[2,3]);b=dict(name='b',source=7,sinks=[1])
    seeds,audit=safe_seeds(graph,[a,b],checkpoint())
    assert not seeds and audit['rejected'][0]['reason']=='other_net_endpoint_occupied'
    seeds,audit=safe_seeds(graph,[a],checkpoint(),blocked_nodes={1})
    assert not seeds and audit['rejected'][0]['reason']=='shared_or_reserved_resource'


def test_corrupt_partial_tree_is_rejected():
    graph=Graph();a=dict(name='a',source=0,sinks=[2,3]);bad=checkpoint();bad['nets']['a']['nodes'].append(6)
    with pytest.raises(ValueError,match='occupancy'):safe_seeds(graph,[a],bad)
    bad=checkpoint();bad['nets']['a']['pips'].append([0,0,'1','2'])
    with pytest.raises(ValueError,match='multiple parents'):safe_seeds(graph,[a],bad)
    bad=checkpoint();bad['nets']['a']['retained_sinks']=[3]
    with pytest.raises(ValueError,match='Disconnected'):safe_seeds(graph,[a],bad)


def test_mutable_resume_can_release_a_previously_shared_branch():
    from ownpr_route_checkpoint import negotiation_trees
    graph=Graph();del graph.edges[7,5];del graph.edges[5,8]
    a=dict(name='a',source=0,sinks=[3],criticality=1.);b=dict(name='b',source=7,sinks=[8],criticality=.5)
    cp=dict(status='incomplete_route_checkpoint',iterations=[dict(iteration=2,overused_nodes=1,unrouted_sinks=0)],nets={
        'a':dict(nodes=[0,1,3],pips=[[0,0,'0','1'],[0,0,'1','3']],retained_sinks=[3]),
        'b':dict(nodes=[7,1,8],pips=[[0,0,'7','1'],[0,0,'1','8']],retained_sinks=[8])})
    trees,info=negotiation_trees(graph,[a,b],cp)
    assert len(trees)==2 and info['overused_nodes']==1 and not info['branches_frozen']
    events=[];result=route(graph,[a,b],warm_checkpoint=cp,progress=events.append)
    assert result['status']=='routed' and 1 not in result['nets']['a']['nodes']
    assert result['iterations'][0]['iteration']==2 and result['iterations'][1]['iteration']==3
    assert events[0]['congestion_history']=='rebuilt_from_current_conflicts_not_exact_replay'
    assert validate(graph,[a,b],result)['no_node_conflicts']


def test_mutable_resume_rejects_corrupt_tree_and_restores_history():
    graph=Graph();net=dict(name='a',source=0,sinks=[2,3]);cp=checkpoint();cp['history']=[[1,7.]]
    events=[];result=route(graph,[net],warm_checkpoint=cp,progress=events.append)
    assert result['status']=='routed' and events[0]['congestion_history']=='restored'
    cp['history']=[[1,-1.]]
    with pytest.raises(ValueError,match='congestion history'):route(graph,[net],warm_checkpoint=cp)
    bad=checkpoint();bad['nets']['a']['nodes'].append(9)
    with pytest.raises(ValueError,match='occupancy'):route(graph,[net],warm_checkpoint=bad)


def test_iteration_limit_result_stays_incomplete_when_converted():
    from ownpr_route_checkpoint import from_unrouted
    n=dict(name='a',source=0,sinks=[2,3]);result=dict(status='unrouted',nets=checkpoint()['nets'],iterations=[],history=[[1,2.]])
    cp=from_unrouted([n],result)
    assert cp['status']=='incomplete_route_checkpoint' and cp['history']==[[1,2.]]
    assert cp['nets']['a']['retained_sinks']==[2] and not cp['no_node_conflicts']
    result['status']='routed'
    with pytest.raises(ValueError,match='explicitly unrouted'):from_unrouted([n],result)


def test_congestion_widens_search_even_when_every_sink_was_connected():
    graph=Graph();del graph.edges[0,4];del graph.edges[7,5];del graph.edges[5,8];widened=[]
    def widen(name):widened.append(name);graph.edges[0,4]=.1
    graph.widen_search=widen
    a=dict(name='a',source=0,sinks=[3],criticality=1.);b=dict(name='b',source=7,sinks=[8],criticality=.5)
    result=route(graph,[a,b],max_iterations=3)
    assert result['iterations'][0]['unrouted_sinks']==0 and result['iterations'][0]['overused_nodes']==1
    assert widened==['a','b'] and result['status']=='routed'
    assert validate(graph,[a,b],result)['no_node_conflicts']
