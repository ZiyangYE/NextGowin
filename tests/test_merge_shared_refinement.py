import copy,sys
from pathlib import Path
import pytest
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from ownpr_image_storage import load_json
from ownpr_device import Device,RoutingGraph
from ownpr_wide_ff_packing import configure_graph
from merge_ownpr_refinement import merge


@pytest.fixture(scope='module')
def example():
    folder=ROOT/'results/vexriscv_shared_global_001';held=folder/'refinement_001/hold/round_000';patch=folder/'trade_route_001'
    graph=RoutingGraph(Device(),ROOT/'results/native_rrg_001');configure_graph(graph,load_json(held/'placement/packed.json'),load_json(held/'placement/placement.json'))
    return graph,load_json(held/'route/nets.json'),load_json(held/'route/routes.json'),load_json(patch/'nets.json'),load_json(patch/'routes.json')


def test_real_tree_reconciliation_preserves_every_other_net(example):
    graph,nets,routes,pnets,proutes=example
    combined,audit=merge(graph,nets,routes,pnets,proutes,['net_4203'])
    assert audit['connectivity_verified'] and audit['no_node_conflicts']
    assert all(combined['nets'][n]==r for n,r in routes['nets'].items() if n!='net_4203')
    assert combined['nets']['net_4203']!=routes['nets']['net_4203']


def test_changed_physical_terminals_and_invalid_patch_cannot_be_admitted(example):
    graph,nets,routes,pnets,proutes=example
    changed=copy.deepcopy(pnets);next(n for n in changed if n['name']=='net_4203')['source']+=1
    with pytest.raises(ValueError,match='terminals'):merge(graph,nets,routes,changed,proutes,['net_4203'])
    bad=dict(proutes,nets=dict(proutes['nets']));bad['nets']['net_4203']=next(r for n,r in routes['nets'].items() if n!='net_4203')
    with pytest.raises(ValueError):merge(graph,nets,routes,pnets,bad,['net_4203'])
