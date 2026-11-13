import copy,json,sys
from pathlib import Path
import pytest
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from ownpr_extended_timing_model import ExtendedTimingModel,report_checks
from audit_ownpr_native_export import audit

FOLDER=ROOT/'results/native_ownpr_uart_export_008';PLACE=ROOT/'results/ownpr_uart_place_001'
def read(p):return json.loads(p.read_text())


@pytest.fixture(scope='module')
def state():
    return ExtendedTimingModel(),read(FOLDER/'extended_timing_queries.json'),read(PLACE/'packed.json'),read(PLACE/'placement.json'),read(FOLDER/'name_map.json')


def test_all_actual_alu_async_and_carry_queries_match(state):
    model,*args=state;r=model.check_capture(*args)
    assert (r['alu_query_vectors'],r['async_query_vectors'],r['carry_edges'])==(1008,438,37)
    assert model.reference_report['checked']==dict(alu_cell_arcs=32,carry_net_arcs=11,async_constraints=146)
    r=report_checks((FOLDER/'probe.tr').read_text(),args[0]);assert r['checked']==dict(alu_cell_arcs=83,carry_net_arcs=43,async_constraints=146)


def test_zero_carry_value_without_actual_sta_edge_is_rejected(state):
    model,data,*rest=state;bad=copy.deepcopy(data);bad['carry_links'][0]['timing_edge_present']=False
    with pytest.raises(ValueError,match='actual carry timing edge'):model.check_capture(bad,*rest)


def test_alu_delay_and_negative_async_constraints_cannot_be_silently_changed(state):
    model,data,*rest=state;bad=copy.deepcopy(data);bad['alu_cells'][0]['queries'][0]['values'][2]+=.1
    with pytest.raises(ValueError,match='ALU timing'):model.check_capture(bad,*rest)
    bad=copy.deepcopy(data)
    next(q for q in bad['async_ff'][0]['queries'] if q['analysis']=='Removal')['values']=[0.]*4
    with pytest.raises(ValueError,match='async constraint'):model.check_capture(bad,*rest)


def test_wrong_async_site_and_missing_query_rejected(state):
    model,data,*rest=state;bad=copy.deepcopy(data);bad['async_ff'][0]['site'][1]+=1
    with pytest.raises(ValueError,match='site/control'):model.check_capture(bad,*rest)
    bad=copy.deepcopy(data);bad['alu_cells'][0]['queries'].pop()
    with pytest.raises(ValueError,match='Incomplete own ALU'):model.check_capture(bad,*rest)


def test_report_arc_corruption_is_rejected(state):
    _,data,*_=state;text=(FOLDER/'probe.tr').read_text();bad=text.replace('0.018   tINS','0.118   tINS',1)
    assert bad!=text
    with pytest.raises(ValueError,match='report mismatch'):report_checks(bad,data)


def test_calibrated_handoff_still_rejects_timing_closure():
    r=audit(FOLDER,PLACE)
    assert r['status']=='initial_handoff_checks_passed' and r['configuration_bits_verified']==19395
    assert r['timing']['report_rows_verified']==1634 and r['timing']['pulse_width_checks']==146
    assert r['timing']['native_carry_timing_model_matched'] and r['timing']['native_async_constraints_matched']
    assert r['timing']['setup_violated_endpoints']==56 and r['timing']['hold_violated_endpoints']==17
    assert not r['native_sta_constraints_satisfied'] and not r['timing_signoff'] and not r['full_bitstream_verified']
