import sys
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from ownpr_batch_budget import consumption,check


def row(name,loss,budget=1.):
    return consumption(name,{1:[2.,2.,2.,2.]},{1:[2.,2.,2.-loss,2.-loss]},{1:{'setup':1.,'hold':budget}})


def test_individually_legal_changes_cannot_spend_hold_budget_twice():
    a,b=row('a',.6),row('b',.6)
    assert check([a])['compatible'] and check([b])['compatible']
    result=check([a,b]);assert not result['compatible']
    assert {v['net'] for v in result['violations']}=={'a','b'}


def test_new_change_rechecks_prior_more_constrained_receiver():
    result=check([row('old',.2,.3),row('new',.2,2.)])
    assert not result['compatible'] and {v['net'] for v in result['violations']}=={'old'}
    assert check([row('old',.1,.3),row('new',.1,2.)])['compatible']


def test_setup_gains_do_not_cancel_another_nets_setup_loss():
    a=consumption('a',{1:[2,2,2,2]},{1:[1,1,2,2]},{1:{'setup':.1,'hold':1}})
    b=consumption('b',{1:[2,2,2,2]},{1:[2.2,2.1,2,2]},{1:{'setup':1,'hold':1}})
    assert not check([a,b])['compatible']
    with pytest.raises(ValueError):check([a,a])
    with pytest.raises(ValueError):row('bad',float('nan'))
