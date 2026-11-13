import copy,json,sys
from pathlib import Path
import pytest
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from ownpr_device import Device,RoutingGraph
from ownpr_carry_macros import validate
from ownpr_route_design import bind
from ownpr_clock_partition import partition
from audit_ownpr_joint_routes import audit


def read(path):return json.loads(path.read_text())


@pytest.fixture(scope='module')
def uart():
    folder=ROOT/'results/ownpr_uart_place_001';device=Device()
    return read(folder/'packed.json'),read(folder/'placement.json'),device,RoutingGraph(device,ROOT/'results/native_rrg_001')


def test_actual_macro_placement_preserves_42_native_alus(uart):
    design,placed,device,_=uart;r=validate(design,placed,device)
    assert r['alu_cells']==42 and r['chains']==5 and r['reserved_cfus']==9 and len(r['links'])==37
    assert not r['arbitrary_boundary_legality_verified']
    proof=read(ROOT/'results/ownpr_uart_place_001/manifest.json')['proof']
    assert proof and all(p['cec']['equivalent'] for p in proof)


def test_broken_progression_and_ordinary_logic_overlap_rejected(uart):
    design,placed,device,_=uart;bad=copy.deepcopy(placed);name=next(iter(bad['arithmetic_clusters']));bad['arithmetic_clusters'][name]['slot']=5
    with pytest.raises(ValueError,match='slot zero|progression'):validate(design,bad,device)
    bad=copy.deepcopy(placed);a=next(iter(bad['arithmetic_clusters'].values()));g=next(iter(bad['clusters'].values()));g['row']=a['row'];g['column']=a['column'];g['slot']=7
    with pytest.raises(ValueError,match='shared with ordinary'):validate(design,bad,device)


def test_carry_endpoints_do_not_become_fake_ordinary_pips(uart):
    design,placed,_,graph=uart;nets,static=bind(design,placed,graph)
    assert len(nets)==265 and sum(n.get('clock',False) for n in nets)==1
    links=[r for r in static if r.get('kind')=='dedicated_carry_link'];assert len(links)==37
    assert all(not r['configuration_mode_verified'] for r in links)
    for row in placed['arithmetic_clusters'].values():
        for n in nets:assert graph.node(row['row'],row['column'],'CIN'+str(row['slot'])) not in n['sinks']


def test_clock_partition_checks_declared_parents_and_all_sinks(uart):
    design,placed,_,graph=uart;nets,_=bind(design,placed,graph);tree=read(ROOT/'results/ownpr_uart_clock_001/clock_tree.json')
    ordinary,check=partition(graph,nets,tree);assert len(ordinary)==264 and check['sinks']==73
    bad=copy.deepcopy(tree);bad['parents'][0]['parent']=[999,999,999]
    with pytest.raises(ValueError,match='parent table'):partition(graph,nets,bad)
    bad=copy.deepcopy(tree);bad['paths_sink_to_source'].pop()
    with pytest.raises(ValueError,match='endpoint set'):partition(graph,nets,bad)


def test_joint_route_audit_does_not_claim_complete_carry_connectivity():
    r=audit(ROOT/'results/ownpr_uart_place_001',ROOT/'results/ownpr_uart_route_003',ROOT/'results/ownpr_uart_wire_001')
    assert r['ordinary_nets']==264 and r['clock_sinks']==73 and r['carry_links']==37
    assert r['no_data_clock_node_conflicts'] and not r['complete_design_connectivity_verified']
