import copy,sys
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from test_general_wide_recompile import grounded
from ownpr_general_wide_recompile import apply
from ownpr_cross_macro_recompile import CrossMacroRecompileFeedback,expanded_boundaries
from ownpr_multilevel_cut import Cuts


def fixture():
    d,l,static,_=grounded(6);m=d['modules']['top'];m['ports']['a']['bits'][0]=9
    m['cells']['upstream']=dict(type='LUT2',parameters={'INIT':'1000'},attributes={},port_directions={'I0':'input','I1':'input','F':'output'},connections={'I0':[9],'I1':[3],'F':[2]})
    l['clusters']['upstream']=dict(lut='upstream',ff=None,row=0,column=1,slot=0)
    f=object.__new__(CrossMacroRecompileFeedback);f.reference=d;f.layout=l;f.static=static
    class Device:grid=[[17,17]]
    f.device=Device()
    return d,l,static,f


def test_upstream_gate_is_absorbed_without_changing_other_consumers():
    d,l,static,f=fixture();boundary=[3,4,5,6,7,9];snapshot=copy.deepcopy(d)
    nd,nl,c=apply(d,l,'root',9,boundary_override=boundary)
    assert d==snapshot and nd['modules']['top']['cells']['upstream']==d['modules']['top']['cells']['upstream']
    assert c['operator']=='cross_macro_recompile'
    assert f.admit(nd,nl,static)['capacity_verified']
    old=Cuts(d,k=7).function('root',boundary);new=Cuts(nd,k=7).function('root',boundary)
    assert (old['cut_bits'],old['truth_hex'])==(new['cut_bits'],new['truth_hex'])
    assert 'upstream' in old['interior'] and 'upstream' not in new['interior']


def test_wrong_global_function_and_mutated_upstream_are_rejected():
    for kind in ('leaf','upstream'):
        d,l,static,f=fixture();nd,nl,_=apply(d,l,'root',9,boundary_override=[3,4,5,6,7,9])
        n='leaf0' if kind=='leaf' else 'upstream';cell=nd['modules']['top']['cells'][n]
        cell['parameters']['INIT']=format(int(cell['parameters']['INIT'],2)^1,'016b' if kind=='leaf' else '04b')
        with pytest.raises(ValueError):f.admit(nd,nl,static)


def test_expansion_never_exposes_private_mux_links():
    d,l,_,_=fixture();model=Cuts(d,k=7,limit=48)
    offered=expanded_boundaries(d,'root',model,[2,3,4,5,6,7])
    assert frozenset([3,4,5,6,7,9]) in offered
    assert not any(set(c)&{100,101,102,103,150,151,200} for c in offered)
