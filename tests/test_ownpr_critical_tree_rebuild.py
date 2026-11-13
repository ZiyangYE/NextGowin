import copy,sys,time
from pathlib import Path
import pytest
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from ownpr_critical_tree_rebuild import eligible,exploratory,endpoint_budgets
from ownpr_whole_net_eco import rebuild
from test_ownpr_whole_net_eco import Graph


def test_branch_search_works_with_passing_feedback_without_rebuilding_other_branches(monkeypatch):
    from ownpr_critical_tree_rebuild import search_candidates
    from test_ownpr_branch_reconnect import Graph as BranchGraph,fixture
    import ownpr_whole_net_eco
    monkeypatch.setattr(ownpr_whole_net_eco,'rebuild',lambda *a,**k:pytest.fail('Unrequested whole-net rebuild'))
    net,record=fixture();owners={n:'n' for n in record['nodes']};owners[5]='clock'
    rows,search=search_candidates(BranchGraph(),net,record,2,owners,time.monotonic()+5,'branch')
    assert rows and 'branch' in search and 'whole' not in search
    assert all((0,0,0,1) in r['pips'] and (0,0,1,3) in r['pips'] and 5 not in r['nodes'] for r in rows)
    assert any(4 in r['nodes'] for r in rows)
    with pytest.raises(ValueError):search_candidates(BranchGraph(),net,record,2,owners,time.monotonic()+5,'unknown')


def test_bounded_branch_timeout_is_recorded_without_promoting_a_candidate(monkeypatch):
    import ownpr_branch_reconnect
    from ownpr_critical_tree_rebuild import search_candidates
    def expire(*a,**k):raise TimeoutError('test budget')
    monkeypatch.setattr(ownpr_branch_reconnect,'reconnect',expire)
    rows,search=search_candidates(None,None,None,None,None,time.monotonic()+5,'branch')
    assert rows==[] and search['branch']['status']=='search_timeout'


def test_target_worst_path_instead_of_longest_wire_on_slack_path():
    from ownpr_critical_tree_rebuild import critical_labels
    candidate=dict(net='n',endpoints=['late/I','long/I','tied/I'])
    paths=[dict(slack_ns=.006,ordinary_wires=[dict(net='n',endpoint='late/I',wire_delay_ns=.7),dict(net='other',endpoint='ignore/I')]),
        dict(slack_ns=.5,ordinary_wires=[dict(net='n',endpoint='long/I',wire_delay_ns=1.2)]),
        dict(slack_ns=.006,ordinary_wires=[dict(net='n',endpoint='tied/I',wire_delay_ns=.2)])]
    assert critical_labels(candidate,paths)==(.006,['late/I','tied/I'])
    with pytest.raises(ValueError,match='witnesses'):critical_labels(candidate,paths[:-1])


def test_feedback_rejects_stale_placement_even_if_routes_are_unchanged(monkeypatch):
    import ownpr_critical_tree_rebuild as model
    p=ROOT/'results/vexriscv_ce_replica_001/placement';r=ROOT/'results/vexriscv_ce_replica_001/route';s=ROOT/'results/vexriscv_51_3mhz_001/export/native'
    model.verify_feedback(p,r,s);read=model.load_json
    def stale(path):
        data=read(path)
        if path==s/'manifest.json':data=copy.deepcopy(data);data['placement_sha256']='0'*64
        return data
    monkeypatch.setattr(model,'load_json',stale)
    with pytest.raises(ValueError,match='matching audited'):model.verify_feedback(p,r,s)


def test_terminal_budgets_allow_safe_slack_use_but_reject_hold_exhaustion():
    before={1:[3.,3.,2.,2.],2:[2.,2.,1.,1.]};after={1:[2.,2.,1.3,1.3],2:[2.1,2.1,1.,1.]}
    assert not eligible(before,after,[1])[0]
    budgets={1:dict(setup=.1,hold=.8,original_setup_slack_ns=.101),2:dict(setup=.2,hold=.2,original_setup_slack_ns=.201)}
    assert eligible(before,after,[1],budgets)[0]
    budgets[1]['hold']=.6
    assert not eligible(before,after,[1],budgets)[0]


def test_missing_receivers_and_opposite_transition_regressions_are_not_hidden():
    before={1:[3.,3.,2.,2.]};after={1:[2.,3.1,2.,2.]}
    assert not eligible(before,after,[1],{1:dict(setup=.2,hold=.2)})[0]
    with pytest.raises(ValueError):eligible(before,{},[1])
    with pytest.raises(ValueError):eligible(before,before,[1],{2:dict(setup=1,hold=1)})


def test_exploration_is_distinct_from_eligibility_and_never_waives_hold_budget():
    before={1:[3.,3.,2.,2.],2:[2.,2.,1.,1.]};after={1:[2.,2.,2.,2.],2:[2.04,2.04,1.,1.]}
    ok,delta=eligible(before,after,[1],{1:dict(setup=.1,hold=.1),2:dict(setup=.01,hold=.1)})
    assert not ok and exploratory(delta,.1) and not exploratory(delta,0)
    changed=copy.deepcopy(delta);changed['budget_violations'][0]['hold_loss_ns']=1.
    assert not exploratory(changed,.1)


def test_seeded_rebuild_keeps_required_old_branch_and_reroutes_other_sink():
    graph=Graph();net=dict(name='n',source=0,sinks=[2,3]);seed=dict(nodes=[0,1,2],pips=[(0,0,'0','1'),(0,0,'1','2')],retained_sinks=[2]);before=copy.deepcopy(seed)
    candidates,info=rebuild(graph,net,3,{5:'clock'},time.monotonic()+5,root_weights=(4.,8.),initial_tree=seed)
    assert candidates and seed==before and graph.root_arrival_weight==.6
    assert all(r['parent'][2]==1 and r['parent'][3]==4 for r in candidates)
    assert all(t['retention']['physical_branches_preserved'] for t in info['trials'])
    with pytest.raises(ValueError):rebuild(graph,net,3,{},time.monotonic()+5,root_weights=())


def test_real_ce_sinks_use_complete_endpoint_budgets_not_global_hold_minimum():
    from ownpr_device import Device,RoutingGraph
    from ownpr_image_storage import load_json,read_text
    sta=ROOT/'results/vexriscv_51_3mhz_001/export/native';design=load_json(ROOT/'results/vexriscv_ce_replica_001/placement/packed.json')
    graph=RoutingGraph(Device(),ROOT/'results/native_rrg_001')
    budgets,fallback=endpoint_budgets(graph,design,load_json(sta/'sta_endpoint_audit.json'),read_text(sta/'probe.tr'))
    net=next(n for n in load_json(ROOT/'results/vexriscv_ce_replica_001/route/nets.json') if n['name']=='net_1392')
    assert len(net['sinks'])==30 and all(budgets[s]['scope']=='terminal_endpoint_slack' for s in net['sinks'])
    assert min(budgets[s]['hold'] for s in net['sinks'])>1
    assert fallback['setup']==pytest.approx(.005) and fallback['hold']==pytest.approx(.122)
def test_infeasible_wns_trade_preserves_hold_and_bounds_setup_regression():
    from ownpr_critical_tree_rebuild import worst_slack_exploratory
    delta=dict(best_critical_gain_ns=2.4,max_setup_loss_ns=.57,predicted_min_sink_setup_slack_ns=-1.94,
        budget_violations=[dict(hold_loss_ns=-.17,budget={'hold':1.67})])
    assert worst_slack_exploratory(delta,-3.437)
    assert not worst_slack_exploratory(delta,0.)
    assert not worst_slack_exploratory(dict(delta,predicted_min_sink_setup_slack_ns=-3.5),-3.437)
    assert not worst_slack_exploratory(dict(delta,max_setup_loss_ns=.76),-3.437)
    assert not worst_slack_exploratory(dict(delta,budget_violations=[dict(hold_loss_ns=.2,budget={'hold':.1})]),-3.437)

