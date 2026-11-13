import copy,sys,math
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from ownpr_bidir_timing import analyze,changed_graph


def edge(a,b,delay,transitions=((0,0),(1,1))):return dict(source=a,destination=b,delay=list(delay),transitions=list(transitions))


def test_competing_hidden_branch_limits_joint_gain_and_reverse_budget():
    g=dict(nodes=['a','b','x','z'],seeds={'a':[3.,3.],'b':[3.2,3.2]},edges=[edge('a','x',[1.,1.]),edge('b','x',[.5,.5]),edge('a','z',[.8,.8])])
    limits={'x':[4.,4.],'z':[3.9,3.9]};old=copy.deepcopy(g);base=analyze(g,limits)
    assert base['required']['a']==pytest.approx([3.,3.])
    after=analyze(changed_graph(g,{0:edge('a','x',[.1,.1])}),limits)
    assert after['endpoints']['x']==pytest.approx(.3)
    assert after['wns']==pytest.approx(.1) and g==old


def test_hold_uses_earliest_path_and_largest_reverse_requirement():
    g=dict(nodes=['a','b','x','z'],seeds={'a':[1.,1.],'b':[.7,.7]},edges=[edge('a','x',[.3,.3]),edge('b','x',[.4,.4]),edge('a','z',[.1,.1])])
    r=analyze(g,{'x':[1.,1.],'z':[1.2,1.2]},early=True)
    assert r['arrival']['x']==pytest.approx([1.1,1.1]) and r['required']['a']==pytest.approx([1.1,1.1])
    assert r['wns']==pytest.approx(-.1) and r['violations']==1


def test_nonunate_rise_fall_requirements_and_atomic_reject():
    g=dict(nodes=['a','x'],seeds={'a':[1.,2.]},edges=[edge('a','x',[.2,.4],((0,0),(0,1),(1,0),(1,1)))])
    r=analyze(g,{'x':[3.,2.7]});assert r['required']['a']==pytest.approx([2.3,2.3])
    old=copy.deepcopy(g)
    with pytest.raises(ValueError,match='cycle'):changed_graph(g,{0:edge('x','x',[0.,0.])})
    with pytest.raises(ValueError,match='delay'):changed_graph(g,{0:edge('a','x',[math.nan,0.])})
    assert g==old


def test_migrated_critical_load_cannot_hide_old_sibling_or_hold_failure():
    g=dict(nodes=['p','r','critical','short','sibling'],seeds={'p':[1.,1.],'r':[.85,.85]},edges=[edge('p','critical',[1.,1.]),edge('p','short',[.3,.3]),edge('r','sibling',[.3,.3])])
    old=copy.deepcopy(g);limits={'critical':[1.9,1.9],'short':[2.,2.],'sibling':[1.5,1.5]}
    # A newly attached load changes the existing sibling's entire tree delay.
    candidate=changed_graph(g,{0:edge('r','critical',[.5,.5]),1:edge('r','short',[.1,.1]),2:edge('r','sibling',[.9,.9])})
    r=analyze(candidate,limits)
    assert r['endpoints']['critical']>analyze(g,limits)['endpoints']['critical']
    assert r['wns']<analyze(g,limits)['wns']
    h=analyze(candidate,{'critical':[1.,1.],'short':[1.1,1.1],'sibling':[1.,1.]},early=True)
    assert h['endpoints']['short']<0 and h['violations']==1 and g==old
