import copy,sys
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from ownpr_route_branch import branches,prune
from ownpr_alias_feedback import AliasFeedback,admissible


class G:
    class device:names={i:str(i) for i in range(30)}
    def node(self,r,c,n):return int(n)
    def check_pip(self,p):return True


def test_complete_receiver_subtree_and_donor_pruning():
    net=dict(name='donor',source=0,sinks=[2,3,5,6]);record=dict(nodes=list(range(7)),pips=[[0,0,str(a),str(b)] for a,b in ((0,1),(1,2),(1,3),(0,4),(4,5),(4,6))])
    old=copy.deepcopy(record);cuts=branches(G(),net,record)
    assert {(c['anchor'],tuple(c['sinks'])) for c in cuts}=={(1,(2,3)),(4,(5,6))}
    kept=prune(G(),net,record,[5,6]);assert kept['nodes']==[0,4,5,6] and len(kept['pips'])==3 and record==old
    bad=copy.deepcopy(record);bad['pips'].append([0,0,'3','2'])
    with pytest.raises(ValueError,match='Invalid'):branches(G(),net,bad)
    with pytest.raises(ValueError,match='remaining'):prune(G(),net,record,[])


def e(a,b,d):return dict(source=a,destination=b,kind='wire',delay=[d,d],transitions=[(0,0),(1,1)])


def test_negative_tns_must_move_toward_zero_at_equal_wns():
    current=dict(setup=dict(wns=-2.,tns=-350.),hold=dict(wns=.13,violations=0))
    better=copy.deepcopy(current);better['setup']['tns']=-340.
    worse=copy.deepcopy(current);worse['setup']['tns']=-360.
    assert admissible(better,current) and not admissible(worse,current)


def test_existing_small_positive_hold_margin_is_preserved():
    current=dict(setup=dict(wns=.003,tns=0.),hold=dict(wns=.002,violations=0))
    better=copy.deepcopy(current);better['setup']['wns']=.02
    assert admissible(better,current)
    better['hold']['wns']=.0018
    assert not admissible(better,current)
    better['hold']=dict(wns=.002,violations=1)
    assert not admissible(better,current)


def test_hold_guard_is_per_endpoint_and_frozen_across_commits():
    reference=dict(setup=dict(wns=.003,tns=0.),hold=dict(wns=.002,violations=0,endpoints={'short':.002,'critical':.7}))
    candidate=copy.deepcopy(reference);candidate['setup']['wns']=.02;candidate['hold']['endpoints']['critical']=.01
    assert not admissible(candidate,reference,reference)
    candidate['hold']['endpoints']['critical']=.06
    assert admissible(candidate,reference,reference)
    current=copy.deepcopy(reference);current['hold']['endpoints']['critical']=.03
    candidate['hold']['endpoints']['critical']=.04
    assert not admissible(candidate,current,reference)


def test_complete_old_sibling_measurement_and_atomic_hold_rejection():
    graph=dict(nodes=['p/Q','r/Q','critical/I0','short/I0','keep/I0','sibling/I0'],seeds={'p/Q':[1.,1.],'r/Q':[.85,.85]},edges=[e('p/Q','critical/I0',1.),e('p/Q','short/I0',.3),e('p/Q','keep/I0',.4),e('r/Q','sibling/I0',.3)])
    model=dict(late=graph,early=copy.deepcopy(graph),setup_requirements={n:[v,v] for n,v in [('critical/I0',1.9),('short/I0',2.),('keep/I0',2.),('sibling/I0',1.5)]},hold_requirements={n:[v,v] for n,v in [('critical/I0',1.),('short/I0',1.1),('keep/I0',1.),('sibling/I0',1.)]})
    captures=[dict(instance=n,pin='I0',nodes=[dict(node=[1,1,11+i])]) for i,n in enumerate(('critical','short','keep','sibling'))]
    current={'setup':graph,'hold':model['early']};old=copy.deepcopy(current)
    ff=lambda bit:dict(type='DFFRE',port_directions={'Q':'output'},connections={'Q':[bit]})
    lut=lambda bit,out:dict(type='LUT1',port_directions={'I0':'input','F':'output'},connections={'I0':[bit],'F':[out]})
    cells={'p':ff(1),'r':ff(2)}
    cells.update({n:lut(bit,20+i) for i,(n,bit) in enumerate([('critical',2),('short',2),('keep',1),('sibling',2)])})
    design={'modules':{'top':{'cells':cells}}}
    reference=copy.deepcopy(design)
    for n in ('critical','short'):reference['modules']['top']['cells'][n]['connections']['I0']=[1]
    f=AliasFeedback(model,captures,G(),reference,{'r':'p'})
    measured={'net_1':{13:[.4]*4},'net_2':{11:[.5]*4,12:[.1]*4}}
    with pytest.raises(ValueError,match='omits an existing receiver'):f.update(current,design,measured)
    measured['net_2'][14]=[.9]*4;candidate=f.update(current,design,measured);metrics=f.metrics(candidate)
    assert not admissible(metrics,f.metrics(current)) and metrics['hold']['violations']==1 and current==old
    bad=copy.deepcopy(design);bad['modules']['top']['cells']['p']['parameters']={'INIT':'1'}
    with pytest.raises(ValueError,match='primitive'):f.update(current,bad,measured)
    shared=copy.deepcopy(measured);shared['net_1'][11]=[.4]*4
    with pytest.raises(ValueError,match='share a physical receiver'):f.update(current,design,shared)
