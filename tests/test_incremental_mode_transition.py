import sys,copy
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from test_ownpr_branch_reuse import Graph,source
from ownpr_incremental_routes import partition,retained_branches
from ownpr_router import route,validate


def test_obsolete_source_mode_is_validated_separately_and_never_reused():
    oldgraph=Graph();newgraph=Graph();del newgraph.edges[1,2];newgraph.edges[4,2]=.2
    net,prior=source();untouched=copy.deepcopy(prior)
    changed,frozen,blocked,audit=partition(newgraph,[net],prior,[net],source_graph=oldgraph)
    assert not frozen and audit['released_for_configuration_change']==['a']
    seeds,_=retained_branches(newgraph,[net],prior,changed,[net],blocked)
    assert seeds['a']['retained_sinks']==[3]
    assert [0,0,'1','2'] not in seeds['a']['pips']
    result=route(newgraph,changed,initial_trees=seeds)
    assert result['status']=='routed' and validate(newgraph,[net],result)['connectivity_verified']
    assert (1,2) not in newgraph.edges and prior==untouched


def test_a_separate_source_graph_is_not_a_validation_bypass():
    oldgraph=Graph();newgraph=Graph();net,prior=source();prior['nets']['a']['pips'].append([0,0,'9','2'])
    with pytest.raises(ValueError,match='Nonexistent'):partition(newgraph,[net],prior,[net],source_graph=oldgraph)


def test_graph_identity_change_is_rejected():
    oldgraph=Graph();newgraph=Graph();newgraph.node=lambda r,c,w:int(w)+1
    net,prior=source()
    with pytest.raises(ValueError,match='identities'):partition(newgraph,[net],prior,[net],source_graph=oldgraph)
