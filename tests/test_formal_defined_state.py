import copy,sys
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from formal_defined_state import propose,operation,add_assertion
from restore_equiv_miter_init import init_values


def fixture():
    cells={}
    for name,q,d in [('known',3,2),('loses_definedness',4,5),('uninitialized',5,2)]:
        cells[name]=dict(type='$ff',connections={'Q':[q],'D':[d]},port_directions={'Q':'output','D':'input'},parameters={'WIDTH':'1'})
    return {'modules':{'product':{'ports':{'d':{'direction':'input','bits':[2]}},'cells':cells,
            'netnames':{'known':{'bits':[3],'attributes':{'init':'0'}},'loses':{'bits':[4],'attributes':{'init':'1'}}}}}}


def test_initialized_state_is_not_assumed_defined_forever():
    result=propose(fixture()['modules']['product'])
    assert result['candidate_bits']==[3] and result['initialized_state_bits']==2 and result['proof_required']
    assert 4 in result['rounds'][0]['rejected_bits']


def test_three_value_controlling_constants_and_unknown_selector():
    assert operation('and',1,7)==1
    assert operation('or',2,7)==2
    assert operation('xor',3,4)==4
    assert operation('mux',1,1,7)==1
    assert operation('mux',1,2,4)==4
    assert operation('mux',3,3,4)&4


def test_unknown_operator_conservatively_removes_candidate():
    d=fixture();m=d['modules']['product'];m['cells']['known']['connections']['D']=[6]
    m['cells']['unknown']=dict(type='unmodeled',connections={'A':[2],'Y':[6]},port_directions={'A':'input','Y':'output'})
    assert propose(m)['candidate_bits']==[]


def test_definedness_assertion_is_side_only_and_preserves_init():
    original=fixture();snapshot=copy.deepcopy(original);result=add_assertion(original,[3]);a=original['modules']['product'];b=result['modules']['product']
    assert original==snapshot and init_values(a)==init_values(b)
    assert all(b['cells'][n]==c for n,c in a['cells'].items())
    assert all(b['ports'][n]==p for n,p in a['ports'].items())
    assert b['cells']['$ng_defined$assert']['type']=='$assert'
    assert not any(c['type']=='$assume' for c in b['cells'].values())


@pytest.mark.parametrize('bits',[[],[5],[3,3]])
def test_uninitialized_or_ambiguous_candidates_reject(bits):
    with pytest.raises(ValueError):add_assertion(fixture(),bits)
