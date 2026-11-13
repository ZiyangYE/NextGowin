import copy,json,sys
from pathlib import Path
import pytest
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from ownpr_clock_slack import ClockSlackModel,score,anchored_slack,minimum
from ownpr_clock_slack_search import reconnect_group,identity,ordered_proposals
from ownpr_timing_numbers import negative_slack


def fixture():
    rows=[dict(node=[1,1,i],delays_by_selector=[[.1]*4,[.2]*4,[.05]*4]) for i in range(3)]
    graph=dict(nodes=['a/Q','b/Q','c/Q','b/D','out/O'],seeds={'a/Q':[.1,.1],'b/Q':[.2,.2],'c/Q':[.11,.11]},edges=[
        dict(source='a/Q',destination='b/D',delay=[1,1],transitions=[(0,0),(1,1)]),
        dict(source='c/Q',destination='b/D',delay=[1,1],transitions=[(0,0),(1,1)]),
        dict(source='b/Q',destination='out/O',delay=[.1,.1],transitions=[(0,0),(1,1)])])
    return dict(graph=graph,clocks=rows,ff_clocks={'a/Q':[1,1,0],'b/Q':[1,1,1],'c/Q':[1,1,2]},
        setup=[dict(endpoint='b/D',transition=0,reported_slack_ns=-.05,capture=[1,1,1]),dict(endpoint='out/O',transition=0,reported_slack_ns=.04,capture=None)],
        constraints=dict(setup=[dict(slack_ns=-.05,launch=[1,1,2],capture=[1,1,1])],
            hold=[dict(slack_ns=.06,capture=[1,1,1])],removal=[],recovery=[]))


def test_capture_delay_helps_setup_but_hurts_output_and_can_break_hold():
    data=fixture();m=ClockSlackModel(data);rows=copy.deepcopy(data['clocks']);rows[1]['delays_by_selector'][1][2]+=.03
    result=m.evaluate(rows);assert result['setup_wns_ns']==pytest.approx(-.02)
    assert result['conservative_bounds_ns']['hold']==pytest.approx(.06) and score(result)>score(m.evaluate(data['clocks']))
    rows[1]['delays_by_selector'][2][0]+=.07
    assert score(m.evaluate(rows)) is None


def test_competing_launch_paths_and_conservative_unreported_hold_bound():
    data=fixture();m=ClockSlackModel(data);rows=copy.deepcopy(data['clocks']);rows[0]['delays_by_selector'][1][2]-=.08
    assert m.evaluate(rows)['setup_wns_ns']==pytest.approx(-.05)
    rows[2]['delays_by_selector'][1][2]-=.08
    assert m.evaluate(rows)['setup_wns_ns']>0
    rows[0]['delays_by_selector'][2][0]=0
    assert m.evaluate(rows)['conservative_bounds_ns']['hold']==pytest.approx(.01)


def test_clock_boundaries_negative_zero_and_quantization_are_fail_closed():
    data=fixture();m=ClockSlackModel(data)
    with pytest.raises(ValueError,match='endpoints'):m.evaluate(data['clocks'][:-1])
    assert negative_slack(anchored_slack(-0.,0.)) and negative_slack(minimum([0.,-0.]))
    data['constraints']['hold'][0]['slack_ns']=-0.
    assert score(ClockSlackModel(data).evaluate(data['clocks'])) is None


def test_shared_prefix_reconnects_siblings_and_preserves_other_subtrees():
    old={5:(0,1,3,5),6:(0,1,3,6),7:(0,1,4,7)}
    new=reconnect_group(old,(0,2,3,5))
    assert new=={5:(0,2,3,5),6:(0,2,3,6),7:(0,1,4,7)} and identity(new)!=identity(old)
    with pytest.raises(ValueError,match='source'):reconnect_group(old,(9,2,3,5))
    with pytest.raises(ValueError,match='Cyclic'):reconnect_group(old,(0,2,0,5))
    with pytest.raises(ValueError,match='Conflicting'):identity({5:(0,1,3,5),6:(0,2,3,6)})


def test_alternative_budget_is_shared_with_critical_clock_endpoints_first():
    class Graph:nodes=[dict(coordinate=[0,0,i]) for i in range(3)]
    rows=list(ordered_proposals({0:['a','b','c'],1:['d','e'],2:['f']},Graph(),{(0,0,1):-.1,(0,0,0):.2}))
    assert rows==[(1,'d'),(0,'a'),(2,'f'),(1,'e'),(0,'b'),(0,'c')]


def test_real_model_reproduces_baseline_and_rejects_prior_latency_regression():
    data=json.loads((ROOT/'results/ownpr_uart_clock_slack_model_001.json').read_text());m=ClockSlackModel(data)
    before=m.evaluate(data['clocks']);assert before['setup_wns_ns']==-.093 and before['setup_violations']==12
    captures=json.loads((ROOT/'results/ownpr_uart_clock_closure_003/export/native/sta_endpoint_audit.json').read_text())
    rows={tuple(r['nodes'][0]['node']):dict(node=r['nodes'][0]['node'],delays_by_selector=r['nodes'][0]['wire_delays']) for r in captures if r['pin']=='CLK'}
    after=m.evaluate(list(rows.values()));assert score(after)<score(before)
    assert after['total_negative_setup_slack_ns']==pytest.approx(-.840,abs=.004)


def test_clock_report_names_are_general_but_other_domains_and_edges_are_rejected():
    from ownpr_clock_slack import parse_constraints
    p=ROOT/'results/ownpr_uart_cascade_pin_closure_001/round_002/export';text=(p/'probe.tr').read_text()
    captures=json.loads((p/'sta_endpoint_audit.json').read_text());coverage=json.loads((p/'handoff_audit.json').read_text())['timing']['path_coverage']
    original=parse_constraints(text,captures,coverage)
    assert parse_constraints(text.replace('clk:[R]','system_clock:[R]'),captures,coverage)==original
    with pytest.raises(ValueError,match='relationship'):parse_constraints(text.replace('Latch Clk         : clk:[R]','Latch Clk         : clk:[F]',1),captures,coverage)


def test_external_hold_proof_rejects_hidden_clocked_competitors():
    from ownpr_clock_slack import external_launch_proof
    def cell(typ,inputs,outputs):return dict(type=typ,connections={**inputs,**outputs},port_directions={**{p:'input' for p in inputs},**{p:'output' for p in outputs}})
    design=dict(modules=dict(top=dict(ports={'input':dict(direction='input',bits=[1])},cells={
        'in':cell('IBUF',{'I':[1]},{'O':[2]}),
        'ff':cell('DFFRE',{'D':[5],'CLK':[9]},{'Q':[4]}),
        'lut':cell('LUT2',{'I0':[2],'I1':[2]},{'F':[5]})})))
    assert external_launch_proof(design,'ff/D')['primary_ibufs']==['in']
    design['modules']['top']['cells']['lut']['connections']['I1']=[4]
    assert external_launch_proof(design,'ff/D') is None
    design['modules']['top']['cells']['lut']['connections']['I1']=[5]
    assert external_launch_proof(design,'ff/D') is None
    design['modules']['top']['cells']['lut']['connections']['I1']=[9]
    assert external_launch_proof(design,'ff/D') is None


def test_proven_external_hold_ignores_unrelated_launch_clock_but_checks_capture():
    data=fixture();data['constraints']['hold'][0]['external_launch_proof']=dict(primary_ibufs=['in'])
    rows=copy.deepcopy(data['clocks']);rows[0]['delays_by_selector'][2][0]=0
    assert ClockSlackModel(data).evaluate(rows)['conservative_bounds_ns']['hold']==pytest.approx(.06)
    rows[1]['delays_by_selector'][2][0]+=.07
    assert score(ClockSlackModel(data).evaluate(rows)) is None
