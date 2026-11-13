import copy,json,sys
from pathlib import Path
import pytest
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from audit_ownpr_native_export import connectivity_check,timing_check
from audit_ownpr_configuration_export import audit as configuration_audit
from audit_ownpr_extended_sta import audit as diagnostic_audit
from ownpr_native_netlist import emit
from ownpr_io_reference import load

PLACE=ROOT/'results/ownpr_uart_place_001'
EXPORT=ROOT/'results/native_ownpr_uart_export_004'
def read(p):return json.loads(p.read_text())


@pytest.fixture(scope='module')
def state():
    return dict(design=read(PLACE/'packed.json'),mapping=read(EXPORT/'name_map.json'),actual=read(EXPORT/'native_connectivity.json'),bindings=read(EXPORT/'binding_report.json'))


def test_real_uart_native_graph_modes_and_inverter_lifting(state):
    r=connectivity_check(**state)
    assert r['pins']==1172 and r['lut_functions']==137 and r['arithmetic_functions']==42
    text,mapping=emit(state['design'])
    assert text==(EXPORT/'probe.vg').read_text() and mapping==state['mapping']
    assert 'LUT4 own_i_0209' in text and "own_i_0209.INIT = 16'h5555" in text


def test_wrong_alu_mode_or_inverter_truth_is_rejected(state):
    bad=copy.deepcopy(state);next(r for r in bad['bindings']['bindings'] if r['kind']=='ALU')['mode']=1
    with pytest.raises(ValueError,match='arithmetic mode'):connectivity_check(**bad)
    bad=copy.deepcopy(state);next(r for r in bad['bindings']['bindings'] if r['native_name']=='own_i_0209')['init_word']=0x5554
    with pytest.raises(ValueError,match='LUT function'):connectivity_check(**bad)


def test_configuration_is_encoded_but_not_timing_signed_off():
    r=configuration_audit(EXPORT,PLACE)
    assert r['status']=='known_configuration_matched' and r['known_configuration_bits']==19395 and not r['mismatches']
    assert r['io']['ports']==24 and r['io_state']['logical_attributes']==384 and r['io_state']['physical_attributes']==384
    assert r['frame_integrity']['frames']==21872
    assert not r['timing_signoff'] and not r['full_bitstream_verified']


def test_full_async_report_covers_every_endpoint_without_signoff(state):
    r=diagnostic_audit(EXPORT,PLACE)
    assert {k:v['endpoints'] for k,v in r['coverage'].items()}==dict(Setup=116,Hold=116,Recovery=73,Removal=73)
    assert sum(r['ordinary_tnet_rows'].values())==1591
    assert len(r['unverified_carry_endpoints'])==37 and not r['carry_timing_verified']
    with pytest.raises(ValueError,match='recovery/removal'):
        timing_check(read(EXPORT/'sta_endpoint_audit.json'),(EXPORT/'probe.tr').read_text(),state['design'],read(PLACE/'placement.json'))


def test_default_truncated_recovery_report_is_rejected():
    with pytest.raises(ValueError,match='Incomplete Recovery'):
        diagnostic_audit(ROOT/'results/native_ownpr_uart_export_003',PLACE)


def test_different_io_contract_cannot_reuse_calibration():
    r=load(ROOT/'results/native_uart_carry_003',EXPORT/'project.cst');assert len(r['state']['io'])==24
    with pytest.raises(ValueError,match='fixed constraint'):
        load(ROOT/'results/native_uart_carry_003',ROOT/'results/native_ownpr_export_020/project.cst')
