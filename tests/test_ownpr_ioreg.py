import json,sys
from pathlib import Path
import pytest
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from ownpr_ioreg_configuration import output_attributes,output_binding,IoregChoices,audit_attributes,audit_reference,audit_encoding
from ownpr_device import Device
from ownpr_place import sha
from audit_native_clock_encoding import audit as clock_audit


def read(p):return json.loads(p.read_text())


def test_all_output_register_control_profiles_match_independent_model():
    result=audit_attributes(ROOT/'results/native_ioreg_attributes_003')
    assert result['cases']==640 and not result['output_direct_link_verified']
    assert output_attributes('DFFCE','signal','signal')[2]==0
    assert output_attributes('DFFRE','signal','signal')[2]==52
    assert output_attributes('DFFPE','one','zero')[28]==55


def test_iob_block_identity_and_neighbor_coordinates_are_retained():
    assert output_binding((110,144,0))['D']==[109,144,7]
    assert output_binding((110,144,1))['D']==[109,145,10]
    assert output_binding((103,183,1))['CLK']==[104,182,124]
    for site in ((0,135,0),(100,100,0),(110,182,1),(109,183,1),(110,135,2)):
        with pytest.raises(ValueError):output_binding(site)
    for args in [('DFF','signal','zero'),('DFFCE','unknown','signal'),('DFFRE','one','zero','one')]:
        with pytest.raises(ValueError):output_attributes(*args)


def test_full_iob_configuration_mask_uses_correct_a_b_table():
    model=IoregChoices(Device());attrs=output_attributes('DFFCE','signal','signal')
    a=model.requirements((110,144,0),attrs);b=model.requirements((110,144,1),attrs)
    assert len(a)==len(b)==100 and not set(a)&set(b)
    assert {p[:2] for p in a}=={(108,143)} and {p[:2] for p in b}=={(108,144)}
    with pytest.raises(ValueError,match='mode'):model.requirements((110,135,0),[0]*171)
    with pytest.raises(ValueError,match='vector'):model.requirements((110,135,0),attrs[:-1])


def test_native_output_links_exist_despite_generic_direct_predicate_false():
    result=audit_reference(ROOT/'results/native_uart_ioreg_calibration_003')
    assert result['required_bits']==1100 and result['output_edges']['edges']==11
    assert result['reference_state_sha256']==sha(ROOT/'results/native_uart_ioreg_calibration_003/ioreg_state.json')
    assert result['output_edges']['report_rows']>=22
    assert result['async_query_report_audit']['io_registers_captured']==11
    assert result['async_query_report_audit']['checked']['async_constraints']==146
    assert not result['own_pnr_result'] and not result['complete_ioreg_timing_model_verified']
    data=read(ROOT/'results/native_uart_ioreg_calibration_003/ioreg_state.json')
    assert all(e['timing_edge_present'] and not e['generic_direct_predicate'] for e in data['output_register_edges'])


def test_output_register_control_differentials_restore_entire_payload():
    result=audit_encoding(ROOT/'results/native_ioreg_attributes_002')
    assert len(result['records'])==7 and result['required_bits']==100
    assert all(r['matched'] and r['full_payload_restored'] for r in result['records'])
    assert not result['route_control_semantics_verified'] and not result['full_bitstream_verified']


def test_constant_control_modes_do_not_prove_routing_can_be_omitted():
    rows=read(ROOT/'results/native_ioreg_attributes_003/ioreg_attributes.json')['records']
    assert all(r['control_route_queries']['CE']['nodes'] and r['control_route_queries']['SR']['nodes'] for r in rows)
    model=IoregChoices(Device());site=(110,135,0)
    assert model.requirements(site,output_attributes('DFFCE','signal','one'))==model.requirements(site,output_attributes('DFFCE','signal','signal'))
    # Different logic sources with identical mode bits still need an actual
    # electrical source; a zero encoding delta cannot certify constant routing.
    assert not audit_attributes(ROOT/'results/native_ioreg_attributes_003')['output_direct_link_verified']


def test_e26_clock_path_is_native_and_encoded_but_not_whole_design_signoff():
    folder=ROOT/'results/native_ioreg_clock_encoding_002';result=clock_audit(folder,folder/'clock_tree.json')
    assert result['status']=='clock_candidate_bits_matched' and result['required_bits']==21
    assert result['actual_changes']==result['predicted_changes']==8 and result['full_payload_restored']
    data=read(ROOT/'results/ownpr_ioreg_clock_field_001/clock_field.json');endpoint=next(r for r in data['sites'] if r['node']==[109,135,124])
    assert endpoint['setup_wire_ns']==pytest.approx(2.111625,abs=1e-6)
    assert not data['timing_signoff'] and not data['configuration_verified']
    with pytest.raises(ValueError,match='direct clock encoding'):
        old=ROOT/'results/native_ioreg_clock_encoding_001';clock_audit(old,old/'clock_tree.json')
