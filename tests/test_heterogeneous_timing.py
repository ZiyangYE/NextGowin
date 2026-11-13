import copy,sys
from pathlib import Path
from types import SimpleNamespace
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from ownpr_heterogeneous_timing import HeterogeneousTiming,fixed_contract


class LUT:
    def lut_input(self,i,s,selector):return [.1,.1,.5,.5]


def fixture():
    def cell(t,pins,outputs,params=None):return dict(type=t,parameters=params or {},port_directions={p:'output' if p in outputs else 'input' for p in pins},connections={p:[v] for p,v in pins.items()})
    cells={'clk':cell('IBUF',{'I':1,'O':9},{'O'}),'a':cell('DFFRE',{'D':2,'Q':3,'CLK':9,'CE':'1','RESET':'0'},{'Q'}),
           'b':cell('DFFRE',{'D':7,'Q':8,'CLK':9,'CE':'1','RESET':'0'},{'Q'}),
           'u':cell('ALU',{'I0':3,'I1':'0','I3':'0','CIN':'0','SUM':4,'COUT':5},{'SUM','COUT'},{'ALU_MODE':'10'}),
           'v':cell('ALU',{'I0':3,'I1':'0','I3':'0','CIN':5,'SUM':7,'COUT':6},{'SUM','COUT'},{'ALU_MODE':'10'})}
    d=dict(modules={'top':dict(cells=cells,ports={},netnames={})})
    late=dict(nodes=['a/Q','b/Q','clk/O','b/D'],edges=[],seeds={'a/Q':[2.,2.],'b/Q':[2.,2.],'clk/O':[.4,.4]});early=copy.deepcopy(late)
    early['seeds']={'a/Q':[.5,.5],'b/Q':[.5,.5],'clk/O':[.2,.2]}
    model=dict(late=late,early=early,setup_requirements={'b/D':[3.,3.]},hold_requirements={'b/D':[.4,.4]})
    alu=SimpleNamespace(alu={(2,s,p,out,selector):[.05,.05,.3,.3] for s in range(6) for p in ('I0','I1','I3','CIN') for out in ('SUM','COUT') for selector in (1,2)})
    mux=SimpleNamespace(delay=lambda *args:[.05,.05,.2,.2])
    return d,model,HeterogeneousTiming(d,model,LUT(),alu,mux,wire_ns=.6,fast_wire_ns=.2)


def test_retained_alu_arrival_propagates_across_boxes_and_charges_ff_adapter():
    d,model,m=fixture();graphs,metrics,info=m.evaluate(d)
    assert metrics['setup']['arrival']['u/SUM']==pytest.approx([2.9,2.9])
    assert metrics['setup']['arrival']['v/SUM']==pytest.approx([3.2,3.2])
    assert metrics['setup']['arrival']['b/D']==pytest.approx([4.3,4.3])
    assert info['ff_adapter_endpoints']==['b/D']
    # Direct v/I0 is faster than the carry-dependent path at the fast corner.
    assert metrics['hold']['arrival']['b/D']==pytest.approx([1.05,1.05])


def test_optimizer_feasibility_changes_no_native_constraint_or_launch():
    d,model,m=fixture();old=copy.deepcopy(model)
    budgets,audit=m.mapping_constraints(d,dict(inputs=['b4'],outputs=['b7']))
    assert budgets['input_arrivals_ps']['b4']==2900
    assert audit['optimization_only_uniform_requirement_shift_ns']==pytest.approx(1.3)
    assert model==old and audit['actual_sta_requirements_unchanged']
    assert m.mapping_constraints(d,dict(inputs=['b4'],outputs=['b7']),False)[1]['optimization_only_uniform_requirement_shift_ns']==0


def test_changed_clock_driver_and_parameters_or_unknown_alu_mode_reject():
    d,model,m=fixture();bad=copy.deepcopy(d);bad['modules']['top']['cells']['b']['connections']['CLK']=[3]
    with pytest.raises(ValueError,match='clock-driver'):m.evaluate(bad)
    bad=copy.deepcopy(d);bad['modules']['top']['cells']['b']['parameters']={'INIT':'1'}
    with pytest.raises(ValueError,match='primitive'):m.evaluate(bad)
    bad=copy.deepcopy(d);bad['modules']['top']['cells']['u']['parameters']['ALU_MODE']='11'
    with pytest.raises(ValueError,match='ALU mode'):m.evaluate(bad)


def test_inactive_native_requirement_is_tracked_through_reverse_wire_delay():
    d,model,m=fixture();model['setup_requirements']['b/D']=[3.,1e10]
    budgets,audit=m.mapping_constraints(d,dict(inputs=['b4'],outputs=['b7']))
    assert budgets['output_required_ps']['b7']<10000
    model['setup_requirements']['b/D']=[3.,1e10-.1]
    with pytest.raises(ValueError,match='native setup requirement'):m.mapping_constraints(d,dict(inputs=['b4'],outputs=['b7']))


def test_vector_hard_outputs_keep_distinct_timing_identity_and_static_clock():
    d,model,m=fixture();d['modules']['top']['cells']['hard']=dict(type='DSP',parameters={},port_directions={'CLK':'input','P':'output'},connections={'CLK':['0'],'P':[40,41]})
    d['modules']['top']['cells']['b']['connections']['D']=[41]
    model['late']['nodes']+=['hard/P[0]','hard/P[1]'];model['late']['seeds'].update({'hard/P[0]':[1.,1.],'hard/P[1]':[4.,4.]})
    model['early']['nodes']+=['hard/P[0]','hard/P[1]'];model['early']['seeds'].update({'hard/P[0]':[.5,.5],'hard/P[1]':[2.,2.]})
    _,metrics,_=m.evaluate(d)
    assert metrics['setup']['arrival']['b/D']==pytest.approx([5.1,5.1])
    assert metrics['hold']['arrival']['b/D']==pytest.approx([2.3,2.3])
    changed=copy.deepcopy(d);changed['modules']['top']['cells']['hard']['connections']['CLK']=['1']
    with pytest.raises(ValueError,match='clock-driver'):m.evaluate(changed)


def test_actual_backend_terminal_bridge_and_dynamic_head_are_charged():
    d,model,m=fixture();d['modules']['top']['cells']['b']['connections']['D']=[6]
    _,metrics,info=m.evaluate(d)
    assert metrics['setup']['arrival']['b/D']==pytest.approx([4.6,4.6])
    assert info['terminal_carry_bridge_outputs']==['v/COUT'] and not info['ff_adapter_endpoints']
    d,model,m=fixture();d['modules']['top']['cells']['u']['connections']['CIN']=[3]
    _,metrics,info=m.evaluate(d)
    assert metrics['setup']['arrival']['u/SUM']==pytest.approx([3.2,3.2])
    assert info['dynamic_carry_head_inputs']==['u/CIN']
