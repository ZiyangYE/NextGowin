import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from select_ownpr_candidate import area_tiebreak,resource_vector
from run_ownpr_pin_closure import improves


def test_resource_tiebreak_never_trades_timing_or_another_resource():
    m=dict(setup_wns_ns=-.075,hold_wns_ns=.037,setup_violations=10,hold_violations=0,total_negative_setup_slack_ns=-.598,reported_fmax_mhz=197.028,other_checks_passed=True,native_constraints_satisfied=False)
    old={'LUT2':10,'DFFRE':4,'ALU':2};new={'LUT4':9,'DFFRE':4,'ALU':2}
    assert area_tiebreak(m,m,old,new)
    assert not area_tiebreak(m,dict(m,setup_wns_ns=-.076),old,new)
    assert not area_tiebreak(m,m,old,dict(new,ALU=3))
    assert not area_tiebreak(m,m,old,old)
    assert not area_tiebreak(dict(m,hold_violations=1),dict(m,hold_violations=1),old,new)


def test_repairing_hard_constraints_is_progress_without_setup_gain():
    good=dict(setup_wns_ns=-1.,hold_wns_ns=.01,setup_violations=2,hold_violations=0,total_negative_setup_slack_ns=-2.,other_checks_passed=True)
    assert improves(dict(good,hold_violations=1,hold_wns_ns=-.1),good)
    assert not improves(good,dict(good,hold_wns_ns=-0.0))
    assert not improves(good,dict(good,hold_violations=1,hold_wns_ns=-.1))
    assert not improves(good,good)
