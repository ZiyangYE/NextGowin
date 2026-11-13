"""Fixture-free checks; no device calibration or external CEC is implied."""
import copy,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from ownpr_multilevel_cut import Cuts,replace
from ownpr_shared_ff_packing import signature
from ownpr_critical_tree_rebuild import worst_slack_exploratory

def evaluate(cells,values):
    values=dict(values);todo=dict(cells)
    while todo:
        ready=False
        for name,c in list(todo.items()):
            p=c['connections'];ins=[p['I'+str(i)][0] for i in range(int(c['type'][-1]))] if c['type'].startswith('LUT') else [p[k][0] for k in ('I0','I1','S0')]
            if any(b not in values for b in ins):continue
            address=sum(values[b]<<i for i,b in enumerate(ins));word=int(c['parameters']['INIT'],2) if c['type'].startswith('LUT') else 0xCA
            values[p['F' if c['type'].startswith('LUT') else 'O'][0]]=(word>>address)&1;del todo[name];ready=True
        assert ready,'Combinational cycle or missing input'
    return values

def test_six_input_remap_preserves_all_input_assignments_and_shared_prefix():
    cells={}
    for n,ins,out,word in [('a',[2,3],10,8),('b',[10,4,5],11,0x96),('root',[11,6,7],12,0xE8)]:
        cells[n]=dict(type='LUT'+str(len(ins)),parameters={'INIT':format(word,f'0{1<<len(ins)}b')},connections={**{f'I{i}':[b] for i,b in enumerate(ins)},'F':[out]},port_directions={**{f'I{i}':'input' for i in range(len(ins))},'F':'output'})
    d={'modules':{'top':{'cells':cells,'ports':{}}}};cut=Cuts(d).function('root',frozenset(range(2,8)));after,_=replace(d,cut,late_bit=2)
    assert after['modules']['top']['cells']['a']==cells['a']
    for v in range(64):
        inp={2+i:(v>>i)&1 for i in range(6)}
        assert evaluate(cells,inp)[12]==evaluate(after['modules']['top']['cells'],inp)[12]

def test_shared_control_identity_is_more_than_same_signal_class():
    a=dict(type='DFFRE',parameters={},connections={'D':[1],'Q':[2],'CLK':[3],'CE':[4],'RESET':['0']})
    b=copy.deepcopy(a);b['connections']['CE']=[5]
    assert signature(a,{'0':'zero','1':'one'})!=signature(b,{'0':'zero','1':'one'})
    b['parameters']['INIT']='1';assert signature(b,{'0':'zero','1':'one'}) is None

def test_negative_slack_trade_does_not_waive_hold():
    delta=dict(best_critical_gain_ns=2.4,max_setup_loss_ns=.57,predicted_min_sink_setup_slack_ns=-1.94,budget_violations=[dict(hold_loss_ns=-.17,budget={'hold':1.67})])
    assert worst_slack_exploratory(delta,-3.437)
    assert not worst_slack_exploratory(delta,0.)
    delta['budget_violations'][0]['hold_loss_ns']=2.;assert not worst_slack_exploratory(delta,-3.437)
