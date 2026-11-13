import copy,sys
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from ownpr_output_state_cache import build,verify


def fixture():
    def cell(t,conn,params=None):return dict(type=t,parameters=params or {},attributes={},connections={p:[b] for p,b in conn.items()},port_directions={p:'output' if p in ('Q','F','O') else 'input' for p in conn})
    cells={'a':cell('DFFRE',dict(D=2,Q=10,CLK=9,CE=4,RESET=6)),'b':cell('DFFRE',dict(D=3,Q=11,CLK=9,CE=5,RESET=7)),
           'predicate':cell('LUT2',dict(I0=10,I1=11,F=12),{'INIT':'0100'}),'pad':cell('OBUF',dict(I=12,O=13))}
    return dict(modules={'top':dict(cells=cells,netnames={},ports={'inputs':dict(direction='input',bits=[2,3,4,5,6,7,9]),'out':dict(direction='output',bits=[13])})})


def test_induction_covers_distinct_enables_reset_priority_and_negated_group():
    old=fixture();new,r=build(old,'pad')
    assert r['proof']['step_vectors']==256 and r['proof']['base_case']
    assert r['output_initial']==0 and len(r['groups'])==2
    # Group A matches zero after reset even when CE is low; it must not be
    # incorrectly masked by !RESET as a positive-literal group would be.
    assert verify(old,new,'pad',r['new_ff'])['step_vectors']==256
    bad=copy.deepcopy(new);ff=bad['modules']['top']['cells'][r['new_ff']];ff['connections']['CE']=['0']
    with pytest.raises(ValueError,match='register'):verify(old,bad,'pad',r['new_ff'])
    bad=copy.deepcopy(new);lut=bad['modules']['top']['cells'][r['added_luts'][-1]];lut['parameters']['INIT']='0'*len(lut['parameters']['INIT'])
    with pytest.raises(ValueError,match='equality'):verify(old,bad,'pad',r['new_ff'])


def test_rejects_async_mixed_clock_unknown_init_and_old_signal_drivers():
    for mutation in ('async','clock','init','synchronizer'):
        d=fixture();a=d['modules']['top']['cells']['a']
        if mutation=='async':a['type']='DFFCE'
        if mutation=='clock':a['connections']['CLK']=[20]
        if mutation=='init':a['parameters']['INIT']='x'
        if mutation=='synchronizer':a['attributes']['ASYNC_REG']='TRUE'
        with pytest.raises(ValueError):build(d,'pad')
    d=fixture();new,r=build(d,'pad');bad=copy.deepcopy(new);bad['modules']['top']['cells'][r['added_luts'][0]]['connections']['F']=[2]
    with pytest.raises(ValueError,match='aliases'):verify(d,bad,'pad',r['new_ff'])
    bad=copy.deepcopy(new);bad['modules']['top']['cells']['a']['connections']['CE']=['1']
    with pytest.raises(ValueError,match='Original'):verify(d,bad,'pad',r['new_ff'])


def test_general_function_preserves_nonzero_group_reset_value():
    d=fixture();c=d['modules']['top']['cells'];c['b']['connections'].update(CE=[4],RESET=[6])
    c['third']=copy.deepcopy(c['a']);c['third']['connections'].update(D=[20],Q=[21],CE=[5],RESET=[7])
    c['predicate']['parameters']['INIT']='0111'
    c['combine']=dict(type='LUT2',parameters={'INIT':'1001'},attributes={},port_directions={'I0':'input','I1':'input','F':'output'},connections={'I0':[12],'I1':[21],'F':[22]})
    c['pad']['connections']['I']=[22];d['modules']['top']['ports']['inputs']['bits'].append(20)
    with pytest.raises(ValueError,match='single-minterm'):build(d,'pad')
    new,r=build(d,'pad',True);assert r['general_function'] and r['output_initial']==0
    assert verify(d,new,'pad',r['new_ff'],True)['step_vectors']==1024
    bad=copy.deepcopy(new);bad['modules']['top']['cells'][r['added_luts'][-1]]['parameters']['INIT']='0000'
    with pytest.raises(ValueError):verify(d,bad,'pad',r['new_ff'],True)


def test_direct_registered_output_is_a_valid_single_state_function():
    d=fixture();d['modules']['top']['cells']['pad']['connections']['I']=[10]
    new,r=build(d,'pad');assert r['proof']['step_vectors']==16
    assert verify(d,new,'pad',r['new_ff'])['base_case']


def test_general_fallback_does_not_depend_on_a_globally_cancelled_state():
    def cell(t,con,init=None):return dict(type=t,attributes={},parameters={} if init is None else {'INIT':init},connections={p:[b] for p,b in con.items()},port_directions={p:'output' if p in ('F','Q','O') else 'input' for p in con})
    c={f'ff{i}':cell('DFFRE',dict(D=2+i,Q=20+i,CLK=99,CE='1',RESET='0')) for i in range(6)}
    c.update(a=cell('LUT1',dict(I0=25,F=100),'10'),b=cell('LUT1',dict(I0=25,F=101),'10'),
        cancel=cell('LUT2',dict(I0=100,I1=101,F=102),'0110'),
        first=cell('LUT4',dict(I0=20,I1=21,I2=22,I3=23,F=103),format(0x6996,'016b')),
        second=cell('LUT2',dict(I0=24,I1=102,F=104),'0110'),root=cell('LUT2',dict(I0=103,I1=104,F=105),'0110'),pad=cell('OBUF',dict(I=105,O=106)))
    d=dict(modules={'top':dict(cells=c,netnames={},ports={'inputs':dict(direction='input',bits=[2,3,4,5,6,7,99]),'out':dict(direction='output',bits=[106])})})
    new,r=build(d,'pad',True)
    assert len(r['states'])==5 and 25 not in r['variables'] and len(r['added_luts'])==3
    assert verify(d,new,'pad',r['new_ff'],True)['step_vectors']==1024
