import copy,sys
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from test_wide_selector_reorder import fixture
from ownpr_existing_wide_remap import apply,info,macro_truth,ExistingWideRemapFeedback
from ownpr_mux_macros import tree


class Device:
    grid=[[17]]


def make_fixture(width):
    d,truth=fixture(width);s=tree(d,'root');l=dict(clusters={n:dict(lut=n,ff=None,row=0,column=0,slot=slot) for n,slot in s['leaves'].items()},
        mux_clusters={n:dict(r,row=0,column=0) for n,r in s['muxes'].items()},wide_macros=[dict(root='root',root_slot={5:0,6:1}[width],row=0,column=0)],fixed_io={})
    static=[dict(r,kind='mux_dedicated_link',row=0,column=0) for r in s['links']]
    f=object.__new__(ExistingWideRemapFeedback);f.reference=d;f.layout=l;f.static=static;f.device=Device()
    return d,l,static,f,truth


@pytest.mark.parametrize('width',[5,6])
def test_truth_recofactoring_is_accepted_with_identical_geometry(width):
    d,l,static,f,truth=make_fixture(width);before=copy.deepcopy(d);placed=copy.deepcopy(l)
    nd,nl,c=apply(d,l,'root',[(2,width-1)])
    assert d==before and l==placed and nl==l
    assert int(macro_truth(nd,'root',range(2,width+2),set(tree(d,'root')['leaves'])|set(tree(d,'root')['muxes']))['truth_hex'],16)==truth
    assert f.admit(nd,nl,static)['capacity_verified']


def test_primary_probe_and_kept_internal_values_prevent_local_rewrite():
    d,l,_,_,_=make_fixture(6);d['modules']['top']['ports']['probe']=dict(direction='output',bits=[100])
    with pytest.raises(ValueError,match='Observed'):info(d,l,'root')
    d,l,_,_,_=make_fixture(6);d['modules']['top']['netnames']['probe']=dict(bits=[100],attributes={'keep':'1'})
    with pytest.raises(ValueError):info(d,l,'root')


def test_wrong_truth_topology_or_geometry_is_not_admitted():
    for mutation in ('truth','link','geometry'):
        d,l,static,f,_=make_fixture(6);nd,nl,_=apply(d,l,'root',[(2,5)])
        if mutation=='truth':
            cell=nd['modules']['top']['cells']['leaf0'];cell['parameters']['INIT']=format(int(cell['parameters']['INIT'],2)^1,'016b')
        elif mutation=='link':nd['modules']['top']['cells']['root']['connections']['I0']=[151]
        else:nl['clusters']['leaf0']['slot']=7
        with pytest.raises(ValueError):f.admit(nd,nl,static)
