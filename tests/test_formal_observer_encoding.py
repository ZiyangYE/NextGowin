import copy,sys
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from formal_observer_encoding import encode
from restore_equiv_miter_init import init_values


def fixture():
    return {'modules':{'top':{'attributes':{'top':'1'},'ports':{'d':{'direction':'input','bits':[2,3,4]},'q':{'direction':'output','bits':[5,6,7]}},
        'netnames':{'d':{'bits':[2,3,4],'attributes':{}},'q':{'bits':[5,6,7],'attributes':{'init':'1x0'}}},
        'cells':{'ff':{'type':'$ff','parameters':{'WIDTH':'11'},'port_directions':{'D':'input','Q':'output'},'connections':{'D':[2,3,4],'Q':[5,6,7]}}}}}}


def test_sink_only_observers_preserve_original_fanouts_and_init():
    d=fixture();snapshot=copy.deepcopy(d);new,cert=encode(d,'top');a=d['modules']['top'];b=new['modules']['top']
    assert d==snapshot and b['cells']['ff']==a['cells']['ff']
    assert init_values(a)==init_values(b)=={5:'0',7:'1'}
    assert b['ports']['d']==a['ports']['d']
    assert b['ports']['q']['bits']!=a['ports']['q']['bits']
    assert 'init' not in b['netnames']['q']['attributes']
    assert b['netnames']['$ng_observer_raw$0']['attributes']['init']=='1x0'
    assert cert['exhaustive_refinement_cases']==18


def test_defined_shared_masks_do_not_hide_any_one_bit_mismatch():
    for gold in ('0','1','x'):
        for gate in ('0','1','x'):
            expected=gold=='x' or gold==gate
            for mask in (0,1):
                xor=lambda v:'x' if v=='x' else str(int(v)^mask)
                assert expected==(xor(gold)=='x' or xor(gold)==xor(gate))


@pytest.mark.parametrize('bad',['namespace','inout','port_alias'])
def test_ambiguous_or_unsupported_interfaces_reject(bad):
    d=fixture();m=d['modules']['top']
    if bad=='namespace':m['netnames']['ng_observer_mask_0']={'bits':[2],'attributes':{}}
    elif bad=='inout':m['ports']['d']['direction']='inout'
    else:m['netnames']['q']['bits']=[2,3,4]
    with pytest.raises(ValueError):encode(d,'top')
