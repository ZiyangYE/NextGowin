import copy,sys
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from ownpr_sta_invariants import compare_envelopes


def example():
    return dict(setup={'ff/D':[6.,10.,4.]},hold={'ff/D':[2.,1.,1.]},
                recovery={'ff/CLEAR':[3.,9.,6.]},removal={'ff/CLEAR':[2.,1.5,.5]})


def test_setup_hold_agreement_cannot_hide_lost_async_checks():
    old=example();new=copy.deepcopy(old);new['recovery']={};new['removal']={}
    with pytest.raises(ValueError,match='recovery endpoint coverage'):compare_envelopes(old,new,allow_expansion=True)


def test_changed_clock_header_with_stale_required_times_is_rejected():
    # The native probe reported 305.388 MHz after changing only clock fields,
    # while required times remained from the 100 MHz run. Reject that shape.
    with pytest.raises(ValueError,match='setup native timing invariants'):compare_envelopes(example(),example(),period_delta_ns=-4.)


def test_valid_period_change_preserves_hold_and_removal():
    old=example();new=copy.deepcopy(old)
    for mode in ('setup','recovery'):
        for row in new[mode].values():row[1]-=2.;row[2]-=2.
    r=compare_envelopes(old,new,period_delta_ns=-2.)
    assert all(max(row['maximum_errors_ns'].values())==0 for row in r.values())
    new['hold']['ff/D'][1]+=.01
    with pytest.raises(ValueError,match='hold native timing invariants'):compare_envelopes(old,new,period_delta_ns=-2.)


def test_expansion_keeps_every_original_witness_and_requires_all_classes():
    old=example();new=copy.deepcopy(old);new['recovery']['other/CLEAR']=[2.,8.,6.]
    compare_envelopes(old,new,allow_expansion=True)
    with pytest.raises(ValueError,match='coverage'):compare_envelopes(old,new)
    del new['removal']
    with pytest.raises(ValueError,match='four analysis'):compare_envelopes(old,new,allow_expansion=True)
