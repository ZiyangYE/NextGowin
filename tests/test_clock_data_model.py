import copy,sys
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from ownpr_clock_data_model import ClockDataModel
from ownpr_ideal_skew_bound import constraints,feasible,bound


def fixture():
    cells={n:dict(type='DFFRE',connections={'CLK':[9]},parameters={}) for n in ('a','b','c')}
    cells['clk']=dict(type='IBUF',connections={'O':[9]});cells['in']=dict(type='IBUF',connections={'O':[8]})
    nodes=['a/Q','b/Q','c/Q','in/O','b/D','out/I'];edges=[]
    for s,d,v in [('a/Q','b/D',2.),('c/Q','b/D',2.1),('in/O','b/D',.4),('b/Q','out/I',.1)]:
        edges.append(dict(source=s,destination=d,delay=[v,v],transitions=[(0,0),(1,1)]))
    late=dict(nodes=nodes,edges=edges,seeds={'a/Q':[1.,1.],'b/Q':[1.,1.],'c/Q':[1.,1.],'in/O':[.2,.2]})
    early=copy.deepcopy(late)
    for e in early['edges']:e['delay']=[.3,.3]
    model=dict(late=late,early=early,setup_requirements={'b/D':[3.,3.],'out/I':[3.,3.]},hold_requirements={'b/D':[.5,.5],'out/I':[.5,.5]})
    capture=[dict(instance=n,pin='CLK',nodes=[dict(node=[1,1,i],wire_delays=[[0.]*4,[1.]*4,[.3]*4])]) for i,n in enumerate(('a','b','c'))]
    return model,dict(modules={'top':dict(cells=cells)}),capture


def test_competing_launch_and_io_boundaries_are_retained():
    model,design,capture=fixture();m=ClockDataModel(model,design,capture);old=copy.deepcopy(model)
    d={n:(0.,0.) for n in m.ff};d['a']=(-1.,0.)
    assert m.evaluate(d)['setup']['endpoints']['b/D']==pytest.approx(-.1)
    d['b']=(.4,.4);r=m.evaluate(d)
    assert r['setup']['endpoints']['b/D']==pytest.approx(.3)
    assert r['setup']['endpoints']['out/I']==pytest.approx(1.5)
    assert r['hold']['endpoints']['b/D']==pytest.approx(-.4)
    assert model==old


def test_different_corners_and_shared_measurement_coverage():
    model,design,capture=fixture();m=ClockDataModel(model,design,capture)
    rows=[dict(node=r['nodes'][0]['node'],delays_by_selector=copy.deepcopy(r['nodes'][0]['wire_delays'])) for r in capture]
    rows[1]['delays_by_selector'][1][2]+=.2
    assert m.measured(rows)['setup']['endpoints']['b/D']==pytest.approx(.1)
    assert m.measured(rows)['hold']==m.baseline['hold']
    rows[1]['delays_by_selector'][2][0]+=.2
    assert m.measured(rows)['hold']['endpoints']['b/D']==pytest.approx(-.2)
    with pytest.raises(ValueError,match='endpoint set'):m.measured(rows[:-1])


def test_generated_multiclock_parameters_and_duplicate_capture_rejected():
    model,design,capture=fixture()
    bad=copy.deepcopy(design);bad['modules']['top']['cells']['a']['connections']['CLK']=[10]
    with pytest.raises(ValueError,match='one scalar'):ClockDataModel(model,bad,capture)
    bad=copy.deepcopy(design);bad['modules']['top']['cells']['clk']['type']='INV'
    with pytest.raises(ValueError,match='primary IBUF'):ClockDataModel(model,bad,capture)
    bad=copy.deepcopy(design);bad['modules']['top']['cells']['a']['parameters']={'INIT':'1'}
    with pytest.raises(ValueError,match='parameters'):ClockDataModel(model,bad,capture)
    with pytest.raises(ValueError,match='Duplicate'):ClockDataModel(model,design,capture+[capture[0]])
    bad=copy.deepcopy(design);bad['modules']['top']['cells']['a']['parameters']={'INIT':'0'}
    assert ClockDataModel(model,bad,capture).ff=={'a','b','c'}
    bad=copy.deepcopy(model);bad['late']['edges'].append(dict(source='clk/O',destination='b/D',delay=[0.,0.],transitions=[(0,0),(1,1)]))
    with pytest.raises(ValueError,match='waveform'):ClockDataModel(bad,design,capture)


def test_difference_constraints_match_full_dag_and_limit_is_only_ideal():
    model,design,capture=fixture();m=ClockDataModel(model,design,capture);period=3.
    result=bound(model,m.ff,period,2.);shifts=result['shifts_ns'];r=m.evaluate({n:(v,v) for n,v in shifts.items()})
    assert r['setup']['wns']+result['ideal_minimum_period_ns']-period>=-1e-6
    assert r['hold']['wns']>=-1e-6
    assert feasible(constraints(model,m.ff),m.ff,result['ideal_minimum_period_ns']-period-.01,2.) is None
    assert not result['clock_route_realizability_proven']
