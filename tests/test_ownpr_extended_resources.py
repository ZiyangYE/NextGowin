import copy,json,sys
from pathlib import Path
import pytest
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from ownpr_device import Device
from ownpr_ff_configuration import FF_CONTROLS,compile_initial,audit as ff_audit
from ownpr_place import pack
from ownpr_route_design import bind
from ownpr_native_netlist import emit
from ownpr_alu_architecture import AluArchitecture
from ownpr_project_contract import inspect
from native_boundary_cec import prove
from audit_ownpr_native_export import timing_check
from ownpr_carry_chains import extract


def four_ff_design():
    cells={};ports={'clk':dict(direction='input',bits=[2]),'d':dict(direction='input',bits=[3]),'ce':dict(direction='input',bits=[4]),'ctrl':dict(direction='input',bits=[5])}
    for i,(typ,pin) in enumerate(FF_CONTROLS.items()):
        q=10+i;ports['q'+str(i)]=dict(direction='output',bits=[q])
        cells[typ]=dict(type=typ,parameters={'INIT':str(int(typ in ('DFFSE','DFFPE')))},attributes={},
            connections=dict(D=[3],CLK=[2],CE=[4],Q=[q],**{pin:[5]}),port_directions=dict(D='input',CLK='input',CE='input',Q='output',**{pin:'input'}))
    return {'modules':{'top':dict(cells=cells,ports=ports,netnames={})}}


def test_all_four_ff_pin_bindings_match_native_capture():
    d=Device();original=d.register_bindings()
    assert ff_audit(ROOT/'results/native_ff_ports_001')['cases']==1024
    for typ,control in FF_CONTROLS.items():
        physical=d.register_bindings_for(typ)
        for slot in range(8):
            assert physical[slot][control]==original[slot]['RESET']
            assert physical[slot]['Q']==original[slot]['Q']


def test_ff_pack_preserves_state_controls_and_proves_next_state(tmp_path):
    before=four_ff_design();after,groups,_,info=pack(before,'top')
    assert len(info['passthrough_luts'])==4
    for name,cell in before['modules']['top']['cells'].items():
        actual=after['modules']['top']['cells'][name]
        assert actual['type']==cell['type'] and actual['parameters']==cell['parameters']
        assert all(actual['connections'][p]==v for p,v in cell['connections'].items() if p!='D')
    proof=prove(before,after,tmp_path/'proof',60)
    assert proof and all(r['cec']['equivalent'] for r in proof)
    text,_=emit(after)
    assert all(typ+' own_i_' in text for typ in FF_CONTROLS)


def test_alu_modes_and_slots_are_native_and_carry_not_guessed():
    alu=AluArchitecture();assert len(alu.bindings)==486
    for typ in alu.types:
        for slot in range(6):
            assert alu.pins(typ,slot,0)['I0']=='B'+str(slot)
            assert alu.pins(typ,slot,1)['I0']=='A'+str(slot)
            assert alu.pins(typ,slot,2)['I3']=='D'+str(slot)
    with pytest.raises(ValueError,match='Unverified'):alu.pins(17,6,0)
    with pytest.raises(ValueError,match='not yet calibrated'):alu.carry_connection((50,90,0),(50,90,1))


def test_real_uart_keeps_native_arithmetic_and_async_coverage_visible():
    folder=ROOT/'results/tangprimer138_suite_001/uart'
    r=inspect(folder/'own/lut4/05_netlist/final.json',folder/'fixed.cst',folder/'clock200.sdc')
    assert r['ports']==24 and r['period_ns']==5
    assert r['primitive_stage_coverage']['ALU']['count']==42
    assert r['primitive_stage_coverage']['DFFCE']['packing_supported']
    assert not r['ready_for_initial_packing'] and not r['full_flow_ready']
    assert {b['resource'] for b in r['timing_coverage_blockers']}=={'DFFCE','DFFPE'}
    assert r['logical_carry_chains']['chain_count']==5
    assert sum(c['length'] for c in r['logical_carry_chains']['chains'])==42


def test_async_signoff_is_not_inferred_from_setup_hold_only():
    with pytest.raises(ValueError,match='recovery/removal'):
        timing_check([], '',four_ff_design(),{})


def test_carry_cycle_is_not_converted_to_a_valid_chain():
    p=ROOT/'results/tangprimer138_suite_001/uart/own/lut4/05_netlist/final.json';module=json.loads(p.read_text())['modules']['top']
    cell=copy.deepcopy(next(c for c in module['cells'].values() if c['type']=='ALU'));cell['connections']['CIN']=cell['connections']['COUT']
    with pytest.raises(ValueError,match='cycle'):extract({'cells':{'cyclic':cell}})


def test_tmds_chain_inventory_keeps_all_native_alu_cells():
    p=ROOT/'results/tangprimer138_suite_001/tmds/own/lut4/05_netlist/final.json';module=json.loads(p.read_text())['modules']['svo_tmds'];r=extract(module)
    assert r['alu_cells']==92 and r['chain_count']==12 and r['max_length']==32
    assert len({n for c in r['chains'] for n in c['cells']})==92 and not r['blockers']
