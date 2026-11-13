import copy,json,sys
from pathlib import Path
import pytest
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from audit_ioreg_preset_timing import audit,check
from ownpr_ioreg_timing import IoregTimingModel
from ownpr_ioreg_macros import relocate,validate,reserved_points


def test_isolated_preset_timing_covers_dynamic_and_always_enabled_ce():
    r=audit(ROOT/'results/native_ioreg_preset_timing_003')
    assert r['attributes']['cases']==640 and r['timing']['cases']==10 and r['timing']['vectors']==210
    assert not r['fixture_byte_identical_to_historical']
    assert not r['integrated_packing_routing_verified'] and not r['constant_control_electrical_mode_verified']


def test_preset_calibration_rejects_missing_constant_case_and_changed_vector():
    data=json.loads((ROOT/'results/native_ioreg_preset_timing_003/ioreg_timing_queries.json').read_text());model=IoregTimingModel()
    bad=copy.deepcopy(data);bad['registers'].pop()
    with pytest.raises(ValueError,match='coverage'):check(bad,model)
    bad=copy.deepcopy(data);bad['registers'][0]['queries'][0]['values'][2]+=.001
    with pytest.raises(ValueError,match='raw library'):check(bad,model)
    bad=copy.deepcopy(data);bad['registers'][0]['queries'][0]['pin']='CLEAR'
    with pytest.raises(ValueError,match='label'):check(bad,model)


def test_preset_constant_enable_packing_preserves_default_one_and_fixed_pad():
    p=ROOT/'results/ownpr_uart_joint_closure_002/round_002/placement'
    design=json.loads((p/'packed.json').read_text());before=json.loads((p/'placement.json').read_text())
    placed,moves,check=relocate(design,before,['tx']);ff=moves[0]['ff'];row=next(r for r in check['records'] if r['instance']==ff)
    assert row['control_pin']=='PRESET' and row['ce_mode']=='one' and row['default_initial_value']==1
    assert row['site']==[109,183,0] and row['pins']['PRESET']==[109,182,127]
    assert placed['fixed_io']==before['fixed_io'] and len(placed['ioreg_clusters'])==10
    assert tuple(row['pins']['CE']) in reserved_points(design,placed)
    good=copy.deepcopy(design);good['modules']['top']['cells'][ff]['parameters']['INIT']='0001'
    assert validate(good,placed)['capacity_verified']
    for value in ('0','x','11'):
        bad=copy.deepcopy(design);bad['modules']['top']['cells'][ff]['parameters']['INIT']=value
        with pytest.raises(ValueError,match='INIT'):validate(bad,placed)
    bad=copy.deepcopy(design);bad['modules']['top']['cells'][ff]['connections']['CE']=['0']
    with pytest.raises(ValueError,match='Constant CE'):validate(bad,placed)


def test_preset_static_enable_is_not_an_ordinary_routed_signal():
    from ownpr_device import Device,RoutingGraph
    from ownpr_route_design import bind
    p=ROOT/'results/ownpr_uart_joint_closure_002/round_002/placement'
    design=json.loads((p/'packed.json').read_text());before=json.loads((p/'placement.json').read_text());placed,moves,_=relocate(design,before,['tx'])
    graph=RoutingGraph(Device(),ROOT/'results/native_rrg_001');nets,static=bind(design,placed,graph)
    records=[r for r in static if r.get('kind')=='ioreg_static_ce'];assert len(records)==1 and records[0]['value']==1
    r,c,i=records[0]['physical_node'];node=graph.node(r-1,c-1,graph.device.names[i])
    assert all(node not in n['sinks'] for n in nets)
    assert records[0]['cell']==moves[0]['ff'] and records[0]['configuration_mode_verified']


def test_preset_capture_requires_the_actual_async_control_label():
    data=json.loads((ROOT/'results/native_ioreg_preset_timing_003/ioreg_timing_queries.json').read_text());model=IoregTimingModel()
    model.check_row(data['registers'][0],'PRESET');assert model.preset_sha256
    bad=copy.deepcopy(data['registers'][0])
    for q in bad['queries']:
        if q['pin']=='PRESET':q['pin']='CLEAR'
    with pytest.raises(ValueError,match='wrong asynchronous control'):model.check_row(bad,'PRESET')


def test_complete_ten_output_register_attempt_preserves_netlist_and_closes_hold():
    from audit_ownpr_native_export import audit as export_audit
    from audit_ownpr_joint_routes import audit as joint_audit
    folder=ROOT/'results/ownpr_uart_tx_ioreg_closure_002';flow=json.loads((folder/'manifest.json').read_text())
    assert flow['status']=='bounded_ioreg_attempt_complete' and flow['wall_seconds']<flow['budget_seconds']<=3600
    assert len(flow['stages'])==14 and all(s['status']=='passed' for s in flow['stages'])
    assert flow['logical_netlist_byte_identical'] and flow['incremental_routing_requested']
    base=ROOT/'results/ownpr_uart_joint_closure_002/round_002/placement'
    assert (base/'packed.json').read_bytes()==(folder/'placement/packed.json').read_bytes()
    r=export_audit(folder/'initial_export',folder/'placement');io=r['timing']['ioreg']
    assert io['registers']==10 and io['static_ce_one_registers']==1 and io['attributes_verified']==1710
    assert io['timing_queries']['query_vectors']==210 and io['timing_report']['cell_or_constraint_rows']==78
    assert r['configuration_bits_verified']==22992 and r['io']['pins_preserved'] and r['io']['ports']==24
    assert r['timing']['reported_fmax_mhz']==185.824 and r['timing']['setup_violated_endpoints']==30 and r['timing']['hold_violated_endpoints']==0
    assert not r['native_sta_constraints_satisfied'] and not r['full_bitstream_verified']
    joint=joint_audit(folder/'placement',folder/'route',folder/'initial_wire');assert joint['clock_sinks']==73
    route=json.loads((folder/'route/manifest.json').read_text());inc=route['incremental']
    assert inc['reused_nets']==251 and inc['rerouted_nets']==2 and inc['removed_nets']==['net_251'] and not inc['fallback_full_routing']
