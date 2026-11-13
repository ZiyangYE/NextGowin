import sys,math
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from ownpr_frequency_policy import reported_fmax_acceptance
from ownpr_frequency_policy import target_evaluation_mhz,target_relative_fmax_acceptance


def metrics():return dict(reported_fmax_mhz=89.273,hold_wns_ns=.133,hold_violations=0,other_checks_passed=True,native_constraints_satisfied=False)


def test_direct_fmax_keeps_full_precision_without_claiming_110_closure():
    r=reported_fmax_acceptance(metrics())
    assert r['accepted'] and r['accepted_fmax_mhz']==89.273 and not r['frequency_replayed'] and not r['full_timing_pass_at_110mhz']


@pytest.mark.parametrize('key,value',[('hold_violations',1),('hold_wns_ns',-.001),('other_checks_passed',False)])
def test_hold_and_other_failures_are_not_hidden_by_fmax(key,value):
    m=metrics();m[key]=value
    assert not reported_fmax_acceptance(m)['accepted']


def test_non_110_evaluation_or_invalid_fmax_rejected():
    with pytest.raises(ValueError,match='110 MHz'):reported_fmax_acceptance(metrics(),100.)
    m=metrics();m['reported_fmax_mhz']=math.nan
    with pytest.raises(ValueError):reported_fmax_acceptance(m)


@pytest.mark.parametrize('name',['reported_fmax_mhz','hold_wns_ns'])
def test_booleans_are_not_numeric_native_metrics(name):
    m=metrics();m[name]=True
    with pytest.raises(ValueError):reported_fmax_acceptance(m)


def test_target_relative_recognition_separates_fmax_gain_from_constraint_closure():
    m=dict(metrics(),reported_fmax_mhz=120.,setup_wns_ns=-.2,setup_violations=3)
    r=target_relative_fmax_acceptance(m,100.,122.4)
    assert r['accepted'] and r['frequency_target_achieved']
    assert not r['full_timing_pass_at_evaluation'] and not r['frequency_replayed']
    assert target_evaluation_mhz(100.)==pytest.approx(122.4)
    assert target_relative_fmax_acceptance(m,100.,122.4)==r
    with pytest.raises(ValueError):target_relative_fmax_acceptance(m,100.,110.)


def test_target_relative_rule_rejects_hold_failure_and_reports_subtarget_frequency():
    m=dict(metrics(),reported_fmax_mhz=119.,setup_wns_ns=-.3,setup_violations=3)
    r=target_relative_fmax_acceptance(m,100.,122.4)
    assert r['accepted'] and not r['frequency_target_achieved']
    m['hold_violations']=1
    assert not target_relative_fmax_acceptance(m,100.,122.4)['accepted']
