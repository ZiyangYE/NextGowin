import sys,copy
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from ownpr_receiver_ranking import augment
from ownpr_critical_tree_rebuild import critical_labels


def cell(kind,ins,out):
    return dict(type=kind,connections={**ins,'F':[out]},port_directions={**{p:'input' for p in ins},'F':'output'})


def test_hidden_competitor_is_ranked_without_inventing_path_witness():
    design={'modules':{'top':{'cells':{'a':cell('LUT1',{'I0':[1]},10),'b':cell('LUT1',{'I0':[2]},20),
        'first':cell('LUT2',{'I0':[10],'I1':[20]},30),'sibling':cell('LUT1',{'I0':[20]},40)}}}}
    nets=[dict(name='net_10',sinks=[1]),dict(name='net_20',sinks=[2,3])]
    ranking=dict(ranked_nets=[dict(net='net_10',driver={},fanout=1,path_occurrences=1,maximum_reported_wire_ns=6.,total_reported_wire_ns=6.,worst_path_slack_ns=-4.,endpoints=['first/I0'])],
        traces=[dict(slack_ns=-4.,ordinary_wires=[dict(net='net_10',endpoint='first/I0')])])
    original=copy.deepcopy(ranking)
    budgets={('first','I0'):dict(original_setup_slack_ns=-4.),('first','I1'):dict(original_setup_slack_ns=-3.3),('sibling','I0'):dict(original_setup_slack_ns=2.)}
    result=augment(ranking,design,nets,budgets)
    assert ranking==original and result['traces']==ranking['traces']
    hidden=result['ranked_nets'][1]
    assert hidden['net']=='net_20' and hidden['path_occurrences']==0
    assert critical_labels(hidden,result['traces'])==(-3.3,['first/I1'])
    assert hidden['receiver_slacks']['sibling/I0']==2.


def test_clock_and_constant_nets_are_not_proposed():
    d={'modules':{'top':{'cells':{'s':cell('LUT1',{'I0':[1]},10),'q':cell('LUT1',{'CLK':[10],'I0':[10]},20)}}}}
    r=dict(ranked_nets=[],traces=[]);b={('q','CLK'):dict(original_setup_slack_ns=-9.),('q','I0'):dict(original_setup_slack_ns=-1.)}
    assert not augment(r,d,[dict(name='net_10',clock=True,sinks=[1])],b)['ranked_nets']
    assert not augment(r,d,[dict(name='net_10',constant_value=0,sinks=[1])],b)['ranked_nets']


def test_native_float_noise_does_not_overrule_measured_wire_priority():
    d={'modules':{'top':{'cells':{'s':cell('LUT1',{'I0':[1]},10),'q':cell('LUT1',{'I0':[10]},20),'z':cell('LUT1',{'I0':[20]},30)}}}}
    r=dict(ranked_nets=[],traces=[])
    # A measured long wire is preferred to a near-zero extra float difference.
    for net,label,delay in [('net_10','q/I0',6.5),('net_20','z/I0',.1)]:
        r['ranked_nets'].append(dict(net=net,driver={},fanout=1,path_occurrences=1,maximum_reported_wire_ns=delay,total_reported_wire_ns=delay,worst_path_slack_ns=-4.239,endpoints=[label]))
        r['traces'].append(dict(slack_ns=-4.239,ordinary_wires=[dict(net=net,endpoint=label)]))
    b={('q','I0'):dict(original_setup_slack_ns=-4.2392139),('z','I0'):dict(original_setup_slack_ns=-4.2392158)}
    result=augment(r,d,[dict(name='net_10',sinks=[1]),dict(name='net_20',sinks=[2])],b)
    assert result['ranked_nets'][0]['net']=='net_10'
