import sys
from pathlib import Path
import pytest
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from ownpr_wire_budget import ledger,WireDependency

def test_no_historical_candidates_cannot_be_reported_as_an_applied_restore():
    from ownpr_budgeted_restore import restoration_sizes
    assert restoration_sizes(0)==[]
    assert restoration_sizes(1)==[1]
    assert restoration_sizes(4)==[4,2,1]

def test_independent_cones_do_not_share_a_budget_but_serial_changes_do():
    def cell(typ,a,b):return dict(type=typ,connections={'I':[a],'O':[b]},port_directions={'I':'input','O':'output'})
    d={'modules':{'top':{'cells':{'a':cell('LUT1',1,2),'ff':cell('DFFRE',2,3),'b':cell('LUT1',3,4)}}}}
    model=WireDependency(d);a={1:[1.,1.,1.,1.]};b={1:[1.6,1.6,1.,1.]};budget={1:dict(setup=1.,hold=1.)}
    changes={'net_1':(a,b),'net_3':(a,b)}
    r=ledger(changes,budget,model.relations(changes));assert r['accepted'] and r['normalized_loss_totals']['setup']==pytest.approx(.6)
    changes={'net_1':(a,b),'net_2':(a,b)}
    assert not ledger(changes,budget,model.relations(changes))['accepted']
    # Unknown hard cells are not assumed to be timing registers.
    d['modules']['top']['cells']['ff']['type']='UNKNOWN_HARD'
    changes={'net_1':(a,b),'net_3':(a,b)}
    assert not ledger(changes,budget,WireDependency(d).relations(changes))['accepted']

def test_cycles_and_unknown_nets_are_charged_conservatively():
    a={1:[1.,1.,1.,1.]};b={1:[1.6,1.6,1.,1.]};budget={1:dict(setup=1.,hold=1.)};changes={'a':(a,b),'b':(a,b)}
    assert not ledger(changes,budget,dict(edges=[('a','b'),('b','a')],global_nets=[]))['accepted']
    assert not ledger(changes,budget,dict(edges=[],global_nets=['a','b']))['accepted']

def test_reconvergent_branches_use_the_longest_charge_path():
    a={1:[1.,1.,1.,1.]};b={1:[1.3,1.3,1.,1.]};budget={1:dict(setup=1.,hold=1.)}
    changes={n:(a,b) for n in ('a','b','c','d')}
    deps=dict(edges=[('a','b'),('a','c'),('b','d'),('c','d')],global_nets=[])
    r=ledger(changes,budget,deps)
    assert r['accepted'] and r['normalized_loss_totals']['setup']==pytest.approx(.9)
    assert r['global_sum_comparison']['setup']==pytest.approx(1.2)

def test_local_budgets_cannot_double_spend_shared_path_slack():
    a={1:[1.,1.,1.,1.]};b={1:[1.6,1.6,1.,1.]};budget={1:dict(setup=1.,hold=1.)}
    assert ledger({'a':(a,b)},budget)['accepted']
    result=ledger({'a':(a,b),'b':(a,b)},budget)
    assert not result['accepted'] and result['normalized_loss_totals']['setup']==pytest.approx(1.2)

def test_both_slow_transitions_and_fast_hold_loss_are_charged():
    before={1:[2.,3.,1.,1.2]};after={1:[1.,3.5,.4,1.1]};budget={1:dict(setup=1.,hold=.5)}
    result=ledger({'a':(before,after)},budget)
    assert result['normalized_loss_totals']['setup']==.5
    assert result['normalized_loss_totals']['hold']==pytest.approx(1.2) and not result['accepted']

def test_unknown_budget_cannot_be_spent_and_gain_does_not_buy_other_path_slack():
    a={1:[2.,2.,1.,1.]};b={1:[1.,1.,1.,1.]};c={1:[3.,3.,1.,1.]}
    assert ledger({'a':(a,b)}, {})['accepted']
    assert not ledger({'a':(a,c)}, {})['accepted']
    result=ledger({'a':(a,b),'b':(a,c)},{1:dict(setup=.5,hold=1.)})
    assert not result['accepted'] and result['normalized_loss_totals']['setup']==2.
    with pytest.raises(ValueError):ledger({'a':(a,{2:b[1]})},{})
