import copy,json,sys
from pathlib import Path
import pytest
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from ownpr_clock_graph import ClockGraph
from ownpr_clock_timing_search import candidate_space,make_tree
from ownpr_clock_configuration import ClockChoices
from ownpr_device import Device
from audit_native_clock_encoding import audit
from ownpr_placement_feedback import load as feedback


def read(p):return json.loads(p.read_text())


@pytest.fixture(scope='module')
def models():return ClockGraph(ROOT/'results/native_clock_inventory_005'),Device()


def test_measured_path_search_selects_real_native_paths(models):
    graph,device=models;folder=ROOT/'results/ownpr_uart_clock_timing_001';tree=read(folder/'clock_tree.json');report=read(folder/'search_report.json');metrics=read(folder/'clock_wire_delays.json')
    assert report['candidate_paths']==494 and report['merged_delay_predictions_exact']
    assert graph.audit_paths(tree['paths_sink_to_source'])['status']=='clock_paths_match'
    actual={tuple(r['node']):r['setup_wire_ns'] for r in metrics['sinks']}
    for row in report['candidate_costs']:
        assert actual[tuple(row['node'])]==min(p['measurement']['setup_wire_ns'] for p in row['alternatives'])
    assert metrics['max_setup_wire_ns']<2.11 and metrics['max_setup_wire_ns']>2.1
    # Existing-placement latency optimization is small and cannot be reported
    # as the much larger clock-aware placement result.
    assert not tree['timing_signoff']


def test_blocked_clock_endpoint_cannot_enter_search(models):
    graph,device=models
    with pytest.raises(ValueError,match='overlaps routed data'):
        candidate_space(graph,device,[85,182,38],[[108,141,124]],blocked=[[108,141,124]])


def test_parent_merge_rejects_incompatible_clock_path(models):
    graph,_=models;tree=read(ROOT/'results/ownpr_uart_clock_timing_001/clock_tree.json');path=tuple(graph.by_coordinate[tuple(p)] for p in reversed(tree['paths_sink_to_source'][0]))
    with pytest.raises(ValueError,match='Conflicting clock parents'):
        make_tree(graph,tree['source'],[path,path[:-2]+path[-1:]])


def test_unknown_clock_name_is_not_an_alias(models):
    graph,device=models;node=next(r for r in graph.nodes if r['name'] is None)
    assert ClockChoices(device,graph).primary_alias_parent(node['coordinate']) is None


def test_clock_field_guided_placement_preserves_logic_and_fixed_io():
    old=ROOT/'results/ownpr_uart_place_001';new=ROOT/'results/ownpr_uart_place_002';a=read(old/'placement.json');b=read(new/'placement.json');field=read(ROOT/'results/ownpr_uart_clock_field_001/clock_field.json')
    assert read(old/'packed.json')==read(new/'packed.json') and a['fixed_io']==b['fixed_io']
    costs={tuple(r['node'][:2]):r['setup_wire_ns'] for r in field['sites']}
    assert len(b['arithmetic_clusters'])==42 and all(costs[r['row']+1,r['column']+1]<=1.5 for r in b['clusters'].values() if r['ff'])
    assert all(p['cec']['equivalent'] for p in read(new/'manifest.json')['proof'])
    measured=read(ROOT/'results/ownpr_uart_clock_002/clock_wire_delays.json')
    assert len(measured['sinks'])==73 and measured['max_setup_wire_ns']<1.26 and measured['setup_rise_sink_spread_ns']<.067


def test_changed_clock_tree_bits_match_independent_native_encoder():
    r=audit(ROOT/'results/native_uart_clock_encoding_002',ROOT/'results/ownpr_uart_clock_timing_001/clock_tree.json')
    assert r['status']=='clock_candidate_bits_matched' and r['required_bits']==901 and r['actual_changes']==241


def test_actual_timing_feedback_is_bound_to_the_unchanged_logic_graph():
    design=read(ROOT/'results/ownpr_uart_place_003/packed.json');priorities,info=feedback(ROOT/'results/native_ownpr_uart_export_009',design)
    assert info['paths']==59 and info['weighted_endpoints']==113
    assert all(0<v<=1 and p not in ('CLK','CIN') for (n,p),v in priorities.items())
    bad=copy.deepcopy(design);cell=next(c for c in bad['modules']['top']['cells'].values() if c['type']=='LUT4');cell['parameters']['INIT']='0'*16
    with pytest.raises(ValueError,match='different logical graph'):feedback(ROOT/'results/native_ownpr_uart_export_009',bad)


def test_better_clock_skew_does_not_hide_fullio_setup_regression():
    before=read(ROOT/'results/native_ownpr_uart_export_008/handoff_audit.json');after=read(ROOT/'results/native_ownpr_uart_export_009/handoff_audit.json')
    assert after['timing']['path_coverage']['setup']['worst_slack_ns']<before['timing']['path_coverage']['setup']['worst_slack_ns']
    assert not after['native_sta_constraints_satisfied'] and not after['timing_signoff']
    feedback_result=read(ROOT/'results/native_ownpr_uart_export_010/handoff_audit.json')
    assert feedback_result['timing']['setup_violated_endpoints']==48
    assert not feedback_result['native_sta_constraints_satisfied']


def test_uart_route_eco_preserves_clock_and_closes_hold_only():
    source=ROOT/'results/ownpr_uart_route_005';eco=ROOT/'results/ownpr_uart_eco_001'
    for name in ('all_nets.json','clock_tree.json','static_connections.json'):
        assert (source/name).read_bytes()==(eco/name).read_bytes()
    changes=read(eco/'manifest.json')['eco_changes']
    assert sum(c['kind']=='hold' for c in changes)==8
    assert read(eco/'joint_route_audit.json')['no_data_clock_node_conflicts']
    r=read(ROOT/'results/native_ownpr_uart_export_011/handoff_audit.json')
    assert r['timing']['hold_violated_endpoints']==0 and r['timing']['path_coverage']['hold']['worst_slack_ns']==.037
    assert r['timing']['setup_violated_endpoints']==48 and not r['native_sta_constraints_satisfied']
    assert r['configuration_bits_verified']==20153 and r['io']['ports']==24
