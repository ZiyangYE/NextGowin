import sys
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from ownpr_independent_ff_inputs import selector,groups
from ownpr_ff_configuration import single_ff_attributes
from audit_native_core_modes import lut_truth


@pytest.mark.parametrize('slot',range(8))
def test_each_ff_selector_competes_with_exact_wide_select_resource(slot):
    assert selector(slot,[s for s in range(8) if s!=slot])=='SEL'+str(slot)
    with pytest.raises(ValueError):selector(slot,[slot])


def test_input_selection_changes_only_own_half_preserving_controls_and_init():
    for slot in range(8):
        for typ in ('DFFRE','DFFSE','DFFCE','DFFPE'):
            old=single_ff_attributes(typ,slot,'signal','inverted','inverted');new=groups(slot,typ,'signal','inverted','inverted')
            diff=[(g,i) for g in range(4) for i in range(19) if old[g][i]!=new[g][i]]
            assert diff==[(slot//2,11+slot%2)] and new[slot//2][11+slot%2]==1


def test_lut_pin_permutation_and_inversion_preserve_exact_boolean_function():
    assert lut_truth(0xaaaa,{0:(3,False)})==0xff00
    assert lut_truth(0xaaaa,{0:(3,True)})==0x00ff
    assert lut_truth(0x8888,{0:(0,False),1:('c1',False)})==0xaaaa
    assert lut_truth(0x8888,{0:(0,False),1:('c0',False)})==0


def test_missing_dependent_pin_or_unavailable_selector_rejects():
    with pytest.raises(ValueError):lut_truth(0x8888,{0:(0,False)})
    for slot in (-1,8,True):
        with pytest.raises(ValueError):selector(slot)
