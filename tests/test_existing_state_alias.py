import sys,copy
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from ownpr_existing_state_alias import rewire,certify,alias_groups


def design():
    def ff(q):return dict(type='DFFRE',parameters={'INIT':'0'},attributes={},port_directions={'D':'input','CE':'input','RESET':'input','CLK':'input','Q':'output'},connections={'D':[2],'CE':['1'],'RESET':['0'],'CLK':[1],'Q':[q]})
    cells={'a':ff(10),'b':ff(11),'lut':dict(type='LUT1',parameters={'INIT':'10'},attributes={},port_directions={'I0':'input','F':'output'},connections={'I0':[10],'F':[12]})}
    return {'modules':{'top':dict(attributes={},cells=cells,netnames={},ports={'clk':dict(direction='input',bits=[1]),'d':dict(direction='input',bits=[2]),'other':dict(direction='input',bits=[3]),'out':dict(direction='output',bits=[12])})}}


def test_existing_alias_rewire_proves_both_induction_steps_and_observations(tmp_path):
    d=design();assert alias_groups(d)==[['a','b']]
    after=rewire(d,'a','b',[('lut','I0')]);r=certify(d,after,'a','b',tmp_path/'proof')
    assert r['status']=='existing_state_alias_rewire_proved' and len(r['steps'])==2
    assert after['modules']['top']['cells']['a']==d['modules']['top']['cells']['a']


def test_same_type_or_controls_do_not_prove_equal_state(tmp_path):
    for i,(pin,value) in enumerate([('D',3),('CLK',3)]):
        d=design();d['modules']['top']['cells']['b']['connections'][pin]=[value]
        after=rewire(d,'a','b',[('lut','I0')])
        with pytest.raises(ValueError):certify(d,after,'a','b',tmp_path/str(i))
    d=design();d['modules']['top']['cells']['b']['parameters']['INIT']='1'
    with pytest.raises(ValueError):certify(d,rewire(d,'a','b',[('lut','I0')]),'a','b',tmp_path/'init')
    d=design();d['modules']['top']['cells']['b']['attributes']['ASYNC_REG']='1'
    with pytest.raises(ValueError):rewire(d,'a','b',[('lut','I0')])


def test_critical_receiver_gain_is_not_overruled_by_relaxed_sibling_wire():
    from prepare_existing_state_alias import discover
    d=design();c=d['modules']['top']['cells'];c['c']=copy.deepcopy(c['b']);c['c']['connections']['Q']=[13]
    c['relaxed']=copy.deepcopy(c['lut']);c['relaxed']['connections']['F']=[14]
    coords={'a':(10,10),'b':(0,2),'c':(8,0),'lut':(0,0),'relaxed':(10,0)}
    p=dict(clusters={n:dict(row=r,column=s) for n,(r,s) in coords.items()},fixed_io={})
    ranking=dict(ranked_nets=[dict(net='net_10',driver=dict(instance='a',pin='Q'),endpoints=['lut/I0','relaxed/I0'])],
        traces=[dict(slack_ns=s,ordinary_wires=[dict(net='net_10',endpoint=e,wire_delay_ns=w)]) for s,e,w in [(-2.,'lut/I0',1.),(-.1,'relaxed/I0',10.)]])
    rows=discover(d,p,ranking,{n+'/Q':[4.,4.] for n in ['a','b','c']})
    assert rows[0]['replica']=='b' and rows[0]['critical_wire_ns']==1.
    d=design();d['modules']['top']['cells']['b']['attributes']['GSREN']='0'
    with pytest.raises(ValueError):rewire(d,'a','b',[('lut','I0')])


def test_hidden_logic_change_and_blackbox_cannot_pass_empty_proof(tmp_path):
    d=design();after=rewire(d,'a','b',[('lut','I0')]);after['modules']['top']['cells']['lut']['parameters']['INIT']='01'
    with pytest.raises(ValueError):certify(d,after,'a','b',tmp_path/'changed')
    d['modules']['top']['attributes']['blackbox']='1'
    with pytest.raises(ValueError,match='blackbox'):certify(d,rewire(d,'a','b',[('lut','I0')]),'a','b',tmp_path/'opaque')
