import copy,sys
from pathlib import Path
import pytest
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
import ownpr_router
from ownpr_pin_feedback import routing_priorities,placement_priorities
from test_ownpr_branch_reuse import Graph

def test_per_sink_priority_controls_connection_order_and_cost(monkeypatch):
    events=[];calls=[];original=ownpr_router.connection_search
    def wrapped(graph,sink,tree,distances,critical,*args,**kw):
        calls.append((sink,critical));return original(graph,sink,tree,distances,critical,*args,**kw)
    monkeypatch.setattr(ownpr_router,'connection_search',wrapped)
    net=dict(name='a',source=0,sinks=[2,3],criticality=.5,sink_criticalities={'2':.1,'3':.9})
    r=ownpr_router.route(Graph(),[net],progress=events.append)
    assert r['status']=='routed' and calls[0]==(3,.9)
    assert ownpr_router.validate(Graph(),[net],r)['connectivity_verified']

def test_forced_warm_reroute_revisits_connected_net():
    net=dict(name='a',source=0,sinks=[3]);g=Graph();r=ownpr_router.route(g,[net]);cp=dict(status='incomplete_route_checkpoint',nets={'a':dict(r['nets']['a'],retained_sinks=[3])},iterations=[])
    events=[];out=ownpr_router.route(g,[net],warm_checkpoint=cp,force_reroute=['a'],progress=events.append)
    assert any(e['event']=='net_start' and e['net']=='a' for e in events)
    assert ownpr_router.validate(g,[net],out)['no_node_conflicts']
    with pytest.raises(ValueError,match='Forced'):ownpr_router.route(g,[net],warm_checkpoint=cp,force_reroute=['missing'])

def test_invalid_sink_weight_and_unknown_endpoint_rejected():
    for values in ({2:float('nan')},{2:1.1},{5:.2},{2:.3,'2':.4}):
        with pytest.raises(ValueError,match='criticality'):ownpr_router.connection_priorities(dict(source=0,sinks=[2],sink_criticalities=values))

def test_pin_priority_annotates_copies_and_aggregates_bus_without_clock():
    budgets={2:dict(original_setup_slack_ns=.1),3:dict(original_setup_slack_ns=4.1)}
    nets=[dict(name='a',source=0,sinks=[2,3,4],criticality=.5)];before=copy.deepcopy(nets)
    rows,info=routing_priorities(nets,budgets)
    assert nets==before and rows[0]['sink_criticalities']==pytest.approx({2:1.,3:0.,4:.5})
    assert info['known_sinks']==2 and info['unknown_sinks']==1
    design={'modules':{'top':{'cells':{'ram':{'port_directions':{'DI':'input','CLK':'input'}}}}}}
    logical={('ram','DI[0]'):budgets[2],('ram','DI[1]'):budgets[3],('ram','CLK'):dict(original_setup_slack_ns=-100.)}
    assert placement_priorities(design,logical)=={('ram','DI'):1.}

def test_real_placement_feedback_covers_inputs_absent_from_worst_path_reports():
    from ownpr_image_storage import load_json
    from ownpr_placement_feedback import load
    sta=ROOT/'results/vexriscv_54_8mhz_001/export/native'
    design=load_json(ROOT/'results/vexriscv_control_batch_place_002/packed.json')
    reported,_=load(sta,design,4.)
    native,info=load(sta,design,4.,ROOT/'results/native_pin_feedback_002')
    assert info['source']=='native_internal_pin_slack'
    assert sum(v>0 and k not in reported for k,v in native.items())==727
    with pytest.raises(ValueError,match='source differs'):load(sta,design,4.,ROOT/'results/native_pin_feedback_001')

def test_global_loop_accepts_only_complete_sta_improvements():
    from run_ownpr_pin_coopt import improves
    old=dict(setup_wns_ns=.021)
    candidate=dict(setup_wns_ns=.3,hold_wns_ns=.1,setup_violations=0,hold_violations=0,native_constraints_satisfied=True)
    assert improves(old,candidate)
    for update in (dict(setup_wns_ns=.02),dict(hold_wns_ns=-.001),dict(hold_violations=1),dict(native_constraints_satisfied=False)):
        assert not improves(old,dict(candidate,**update))
