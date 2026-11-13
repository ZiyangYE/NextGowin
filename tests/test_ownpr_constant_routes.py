import copy,json,sys
from pathlib import Path
import pytest
ROOT=Path(__file__).resolve().parents[1];sys.path[:0]=[str(ROOT/'scripts'),str(ROOT/'experiments')]
from ownpr_constant_routes import owner,source_check,audit
from ownpr_constant_route_probe import fixture
from ownpr_lut_lifting import independent


def test_bus_pin_resolution_does_not_truncate_or_accept_out_of_range_bits():
    from ownpr_constant_routes import connection_bit
    c=dict(connections={'D':[3,4],'CE':[5]})
    assert connection_bit(c,'D[1]')==4 and connection_bit(c,'CE')==5
    for pin in ('D','D[2]','D[-1]','CE[0]'):
        with pytest.raises(ValueError):connection_bit(c,pin)


def test_cpu_hard_bus_constant_forests_match_native_owners_and_paths():
    from ownpr_device import Device
    folder=ROOT/'results/vexriscv_own_sta_capture_001'
    design=json.loads((ROOT/'results/vexriscv_own_hard_place_007/packed.json').read_text())
    result=audit(folder,design,Device())
    assert result['status']=='constant_route_ownership_and_native_paths_matched'
    assert {r['value']:len(r['sinks']) for r in result['networks']}=={0:307,1:38}
    assert not result['full_bitstream_verified']


def row(value=0):
    nodes=[[10,20,278-value],[11,20,278-value]]
    return dict(sources=nodes,parents=[dict(node=n,parent=None) for n in nodes],clock=False)


def test_constant_forest_uses_unique_logical_owner_with_correct_polarity():
    nets={'ground':100,'power':200};pointers={0:{100},1:{200}};ids={0:278,1:277}
    for value in (0,1):
        pointer,record=owner('constant_'+str(value),row(value),nets,pointers,ids)
        assert pointer==100*(value+1) and len(record['physical_sources'])==2
    with pytest.raises(ValueError,match='polarity'):owner('constant_1',row(0),nets,pointers,ids)
    with pytest.raises(ValueError,match='logical owner'):owner('constant_0',row(),nets,{0:{100,200}},ids)
    with pytest.raises(ValueError,match='polarities'):owner('constant_0',row(),nets,{0:{100},1:{100}},ids)
    bad=row();bad['parents'].pop()
    with pytest.raises(ValueError,match='roots'):source_check('constant_0',bad,ids)


def test_real_fixture_constants_are_essential_and_all_function_assignments_match():
    p=ROOT/'results/ownpr_uart_prune_closure_002/round_001/placement'
    original=json.loads((p/'packed.json').read_text());before=copy.deepcopy(original)
    changed,records=fixture(original,json.loads((p/'placement.json').read_text()))
    assert original==before and [r['value'] for r in records]==[0,1,0,1]
    for r in records:
        c=changed['modules']['top']['cells'][r['instance']]
        assert not independent(c,1)
        for value in (0,1):assert int(c['parameters']['INIT'],2)>>(value+2*r['value'])&1==value


def test_native_constant_probe_has_full_paths_cec_configuration_and_io_timing():
    from ownpr_device import Device
    folder=ROOT/'results/ownpr_constant_route_probe_001';state=json.loads((folder/'manifest.json').read_text())
    assert state['status']=='constant_fixture_complete' and state['calibration_only']
    assert all(r['cec']['equivalent'] and r['observed_hard_inputs_and_ports']==328 for r in state['proof'])
    result=audit(folder/'export/native',json.loads((folder/'placement/packed.json').read_text()),Device())
    assert {r['value'] for r in result['networks']}=={0,1}
    assert all(r['sources']==len(r['sinks'])==2 for r in result['networks'])
    assert state['configuration_bits_verified']>22000
    assert state['metrics']['reported_fmax_mhz']==195.017 and state['metrics']['hold_violations']==0
    assert not state['timing_signoff'] and not state['full_bitstream_verified']


def test_native_audit_rejects_changed_owner_and_sink_physical_source(tmp_path):
    import shutil
    from ownpr_device import Device
    folder=ROOT/'results/ownpr_constant_route_probe_001';source=folder/'export/native'
    for name in ('expanded_routes.json','binding_report.json','native_connectivity.json','sta_endpoint_audit.json'):
        shutil.copyfile(source/name,tmp_path/name)
    design=json.loads((folder/'placement/packed.json').read_text());device=Device()
    path=tmp_path/'binding_report.json';original=path.read_text();data=json.loads(original)
    data['constant_routes'][0]['native_net_names']=['forged_owner'];path.write_text(json.dumps(data))
    with pytest.raises(ValueError,match='ownership'):audit(tmp_path,design,device)
    path.write_text(original)
    path=tmp_path/'sta_endpoint_audit.json';data=json.loads(path.read_text())
    record=next(r for r in data if r['instance']=='ng_ownpr_pass_0' and r['pin']=='I1')
    record['nodes'][0]['chain'][-1][-1]=device.ids['VCC'];path.write_text(json.dumps(data))
    with pytest.raises(ValueError,match='tie source'):audit(tmp_path,design,device)
