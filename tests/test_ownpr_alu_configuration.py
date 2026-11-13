import json,sys
from pathlib import Path
import pytest
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from ownpr_device import Device
from ownpr_alu_configuration import Mode2Configuration
from audit_native_alu_encoding import audit
from audit_native_carry_placement import audit as placement_audit
from ownpr_ff_configuration import independent_ff_groups


def test_constant_profiles_reproduce_all_uart_alu_truth_words():
    r=audit(ROOT/'results/native_alu_encoding_005')
    assert r['profiles']==5 and r['reference_cells_matched']==42 and not r['reference_uncovered']
    assert not r['carry_routing_verified'] and not r['full_bitstream_verified']


def test_nonconstant_mode2_is_slot_independent_in_calibration():
    r=audit(ROOT/'results/native_alu_encoding_004')
    assert {v['slot'] for v in r['records']}==set(range(6))
    assert {v['truth_word'] for v in r['records']}=={0x609a}
    assert r['reference_cells_matched']==0 and len(r['reference_uncovered'])==42


def test_fragment_requires_calibrated_input_profile_and_combined_controls():
    model=Mode2Configuration(Device())
    assert model.word(2,(0,'signal',1,'signal'))==0xc0c0
    assert model.word(2,(1,'signal',1,0))==0x303f
    with pytest.raises(ValueError,match='Uncalibrated'):model.word(1,(0,'signal',1,'signal'))
    with pytest.raises(ValueError,match='Uncalibrated'):model.word(2,('inverted','signal',1,'signal'))
    row=json.loads((ROOT/'results/native_alu_encoding_005/alu_encoding_probe.json').read_text())['records'][0]
    assert model.requirements(49,89,0,2,row['input_profile'],row['cls_attributes'][0])
    bad=list(row['cls_attributes'][0]);bad[0]=9
    with pytest.raises(ValueError,match='Combined CLS'):model.requirements(49,89,0,2,row['input_profile'],bad)


def test_native_carry_adjacency_is_not_misreported_as_own_routing():
    r=placement_audit(ROOT/'results/native_uart_carry_001',ROOT/'results/tangprimer138_suite_001/uart/own/lut4/05_netlist/mapped.vg','top')
    assert r['cells']==42 and r['chains']==5 and r['within_cfu']==33 and r['east_bridges']==4
    assert all(c['native_direct_query'] is False for c in r['connections'])
    assert not r['own_pnr_result'] and not r['arbitrary_location_legality_verified']


def test_zero_encoding_effect_cannot_certify_an_alu_binding():
    with pytest.raises(ValueError,match='Empty'):audit(ROOT/'results/native_alu_encoding_001')


def test_explicit_arithmetic_mode_needs_no_dummy_lut():
    r=audit(ROOT/'results/native_alu_encoding_006')
    assert r['reference_cells_matched']==42 and not r['reference_uncovered']
    row=json.loads((ROOT/'results/native_alu_encoding_006/alu_encoding_probe.json').read_text())['records'][0]
    # This isolated fixture starts from an empty CLS, not an active FF's
    # defaults. Only the arithmetic selectors should be installed.
    attrs=Mode2Configuration.with_alu_modes([[0]*19 for _ in range(4)],[0])
    assert attrs==row['cls_attributes']
    ff=independent_ff_groups([dict(cell_type='DFFRE',slot=0,ce='signal',control='signal',clock='signal')])
    merged=Mode2Configuration.with_alu_modes(ff,[0])
    assert all(merged[g][i]==ff[g][i] for g in range(4) for i in range(19) if (g,i) not in ((0,0),(0,16)))
    with pytest.raises(ValueError,match='duplicate'):Mode2Configuration.with_alu_modes(attrs,[0,0])
