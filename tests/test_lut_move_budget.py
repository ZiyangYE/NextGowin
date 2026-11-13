import copy,sys
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from ownpr_lut_move_budget import coupled_budget,combined_budget


def fixture():
    incoming=[dict(pin='I0',old_node=1,before=[1.,1.,.4,.4],after=[1.3,1.1,.45,.45])]
    outgoing=[dict(sink=2,before=[2.,2.,.5,.5],after=[1.5,1.5,.6,.6])]
    budgets={n:dict(setup=.1,hold=.1) for n in (1,2,3)}
    return incoming,outgoing,budgets


def test_adjacent_output_improvement_can_pay_for_input_loss():
    i,o,b=fixture();r=coupled_budget(i,o,{},b)
    assert r['accepted'] and r['normalized_loss_totals']=={'setup':0.,'hold':0.}
    assert r['coupled_paths'][0]['loss_ns']['setup']==pytest.approx(-.2)


def test_unrelated_branch_cannot_spend_that_improvement():
    i,o,b=fixture();side={'n':[dict(sink=3,before=[.4,.4,.3,.3],after=[.6,.4,.3,.3])]}
    r=coupled_budget(i,o,side,b)
    assert not r['accepted'] and r['normalized_loss_totals']['setup']==pytest.approx(2.)


def test_unknown_transition_polarity_uses_worst_combinations():
    i,o,b=fixture();i[0]['after'][:2]=[1.2,.5];o[0]['after'][:2]=[1.5,2.2]
    r=coupled_budget(i,o,{},b)
    assert not r['accepted'] and r['normalized_loss_totals']['setup']==pytest.approx(4.)


def test_hold_and_missing_budgets_remain_hard_guards():
    i,o,b=fixture();i[0]['after'][2:]=[.3,.3];o[0]['after'][2:]=[.3,.3]
    assert not coupled_budget(i,o,{},b)['accepted']
    del b[2];del b[1];r=coupled_budget(i,o,{},b)
    assert not r['accepted'] and r['normalized_loss_totals']['hold'] is None
    import json
    json.dumps(r,allow_nan=False)
    i[0]['after'][0]=float('nan')
    with pytest.raises(ValueError,match='vector'):coupled_budget(i,o,{},b)


def test_native_neighbor_enumeration_sees_edges_absent_from_worst_path_report():
    from ownpr_pack_carry_neighbors import native_neighbor_ranking
    cells={
        'a':dict(type='ALU',connections={'SUM':[1]},port_directions={'SUM':'output'}),
        'lut':dict(type='LUT1',connections={'I0':[1],'F':[2]},port_directions={'I0':'input','F':'output'}),
        'b':dict(type='ALU',connections={'I0':[2],'CIN':[2],'SUM':[3]},port_directions={'I0':'input','CIN':'input','SUM':'output'})}
    d={'modules':{'top':{'cells':cells}}};ranking={'ranked_nets':[]}
    result,audit=native_neighbor_ranking(d,ranking,{('lut','F'):{'original_setup_slack_ns':.7}},{'net_1':2.1,'net_2':1.2})
    assert audit['added_logical_neighbor_edges']==2
    assert {tuple(r['endpoints']) for r in result['ranked_nets']}=={('lut/I0',),('b/I0',)}
    assert all(r['worst_path_slack_ns']==.7 for r in result['ranked_nets'])
    assert not ranking['ranked_nets']


def test_unmeasured_proposals_do_not_reserve_scarce_spare_slots():
    from ownpr_pack_carry_neighbors import propose
    cells={n:dict(type='LUT1',connections={'I0':[0],'F':[i]},port_directions={'I0':'input','F':'output'}) for i,n in enumerate(('a','b','c'),1)}
    cells['alu']=dict(type='ALU',connections={'I0':[1],'I1':[2],'I3':[3],'SUM':[4]},port_directions={'I0':'input','I1':'input','I3':'input','SUM':'output'})
    d={'modules':{'top':{'cells':cells}}};layout=dict(clusters={n:dict(lut=n,ff=None,row=4+i,column=4,slot=0) for i,n in enumerate(('a','b','c'))},arithmetic_clusters={'alu':dict(row=1,column=1,slot=0)},fixed_io={})
    ranking={'ranked_nets':[dict(driver={'instance':n},endpoints=['alu/'+pin],worst_path_slack_ns=i*.1,maximum_reported_wire_ns=1.) for i,(n,pin) in enumerate(zip(('a','b','c'),('I0','I1','I3')))]}
    original=copy.deepcopy(layout);applied,choices=propose(d,layout,ranking,{},count=3,independent=True)
    assert applied is None and layout==original
    assert {c['lut'] for c in choices}=={'a','b','c'}
    assert all(c['new_site']==[1,1,6] for c in choices)


def test_through_pin_intersection_does_not_import_unrelated_tight_path():
    i,o,b=fixture();i[0]['after']=[1.2,1.2,.4,.4];o[0]['after']=o[0]['before'][:]
    b[1]['setup']=3.;b[2]['setup']=.1
    i.append(dict(pin='I1',old_node=3,before=[1.,1.,.4,.4],after=[1.,1.,.4,.4]))
    r=coupled_budget(i,o,{},b)
    assert r['accepted'] and r['normalized_loss_totals']['setup']==pytest.approx(.2/3.)


def test_group_charges_accumulate_only_on_conservatively_reachable_paths():
    class Dependency:
        def __init__(self,edges):self.edges=edges
        def relations(self,names):return dict(edges=self.edges,global_nets=[])
    moves=[dict(lut='a',affected_nets=['net_1'],budget={'normalized_loss_totals':{'setup':.6,'hold':.2}}),
           dict(lut='b',affected_nets=['net_2'],budget={'normalized_loss_totals':{'setup':.7,'hold':.3}})]
    assert combined_budget(moves,Dependency([]))['accepted']
    r=combined_budget(moves,Dependency([('net_1','net_2')]))
    assert not r['accepted'] and r['normalized_loss_totals']['setup']==pytest.approx(1.3)
