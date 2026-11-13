import copy,sys
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from restore_equiv_miter_init import restore,init_values


def pair(value='1x0'):
    def module(offset,initial):
        n={'d':dict(bits=[1+offset,2+offset,3+offset],attributes={}),
           'q':dict(bits=[4+offset,5+offset,6+offset],attributes={'init':value} if initial else {})}
        c={'f':dict(type='$ff',parameters={'WIDTH':'11'},port_directions={'D':'input','Q':'output'},connections={'D':n['d']['bits'],'Q':n['q']['bits']})}
        return dict(cells=c,netnames=n,ports={})
    return {'modules':{'equiv':module(0,True)}},{'modules':{'proof_base':module(10,False)}}


def test_preserves_zero_one_and_undefined_without_changing_logic():
    a,b=pair();original=copy.deepcopy(b);r,c=restore(a,b)
    assert b==original and r['modules']['proof_base']['cells']==b['modules']['proof_base']['cells']
    assert init_values(r['modules']['proof_base'])=={14:'0',16:'1'}
    assert c['defined_state_bits']==2


@pytest.mark.parametrize('mutation',['initial','wiring','type','name'])
def test_conflicting_initial_or_state_identity_rejects(mutation):
    a,b=pair();m=b['modules']['proof_base']
    if mutation=='initial':m['netnames']['q']['attributes']['init']='0x0'
    elif mutation=='wiring':m['cells']['f']['connections']['D'][0]=14
    elif mutation=='type':m['cells']['f']['parameters']['WIDTH']='10'
    else:m['cells']['unknown']=m['cells'].pop('f')
    with pytest.raises(ValueError):restore(a,b)


def test_no_initial_state_is_invented():
    a,b=pair('xxx');r,c=restore(a,b)
    assert c['defined_state_bits']==0 and not init_values(r['modules']['proof_base'])
