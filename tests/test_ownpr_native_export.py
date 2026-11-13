import copy,json,sys
from pathlib import Path
import pytest
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from audit_ownpr_native_export import connectivity_check,timing_check,io_check,audit
from ownpr_place import constraints
from ownpr_native_netlist import emit

FOLDER=ROOT/'results/native_ownpr_export_012'
PLACE=ROOT/'results/ownpr_int2float_place_004'
def read(path):return json.loads(path.read_text())


@pytest.fixture(scope='module')
def state():
    return dict(design=read(PLACE/'packed.json'),mapping=read(FOLDER/'name_map.json'),
        actual=read(FOLDER/'native_connectivity.json'),bindings=read(FOLDER/'binding_report.json'))


def test_native_graph_and_constant_alias_match(state):
    result=connectivity_check(**state)
    assert result['pins']==518 and result['lut_functions']==96 and result['constant_aliases']==1


def test_wrong_native_net_is_rejected(state):
    changed=copy.deepcopy(state);changed['actual'][0]['net']='wrong_net'
    with pytest.raises(ValueError,match='connectivity'):connectivity_check(**changed)


def test_wrong_truth_table_is_rejected(state):
    changed=copy.deepcopy(state)
    next(r for r in changed['bindings']['bindings'] if r['kind']=='LUT')['init_word']^=1
    with pytest.raises(ValueError,match='LUT function'):connectivity_check(**changed)


def test_estimate_mode_is_not_accepted_as_routed_sta(state):
    captures=read(FOLDER/'sta_endpoint_audit.json');placed=read(PLACE/'placement.json')
    with pytest.raises(ValueError,match='differs'):
        timing_check(captures,(ROOT/'results/native_ownpr_export_003/probe.tr').read_text(),state['design'],placed)


def test_truncated_reports_fail_endpoint_coverage(state):
    old=ROOT/'results/native_ownpr_export_011'
    with pytest.raises(ValueError,match='endpoint report'):
        timing_check(read(old/'sta_endpoint_audit.json'),(old/'probe.tr').read_text(),state['design'],read(PLACE/'placement.json'))


def test_unknown_bank_voltage_is_rejected(state):
    contract=constraints(state['design']['modules']['top'],(FOLDER/'project.cst').read_text(),(FOLDER/'probe.sdc').read_text())
    old=(ROOT/'results/native_ownpr_export_009/probe.pin.html').read_text()
    with pytest.raises(ValueError,match='electrical'):io_check(old,contract)


def test_native_format_and_scalar_port_names_preserved(state):
    text,mapping=emit(state['design'])
    assert text==(FOLDER/'probe.vg').read_text()
    assert mapping==state['mapping']
    assert '\\a[10] ' in text


def test_full_handoff_checks_do_not_claim_timing_closure():
    result=audit(FOLDER)
    assert result['configuration_bits_verified']==11755
    assert result['timing']['report_rows_verified']==188
    assert result['timing']['pulse_width_checks']==36
    assert result['timing']['all_setup_hold_endpoints_displayed']
    assert not result['timing']['setup_hold_passed']
    assert not result['timing_signoff'] and not result['full_bitstream_verified']


def test_hold_eco_result_preserves_fullio_and_remains_honest():
    result=audit(ROOT/'results/native_ownpr_export_013',partial=ROOT/'results/ownpr_clock_eco_partial_002/partial_configuration.json')
    assert result['timing']['hold_violated_endpoints']==0
    assert result['timing']['setup_violated_endpoints']==1
    assert result['io']['ports']==19 and result['io']['pins_preserved']
    assert result['configuration_bits_verified']==11816
    assert not result['timing_signoff']
