import copy,sys
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from ownpr_device import Device
from ownpr_multilevel_cut import Cuts,replace
from ownpr_timing_wide_remap import place
from ownpr_macro_legalize import clear_site
from ownpr_mux_macros import validate
from ownpr_mux_configuration import compile_wide
from test_ownpr_multilevel_cut import sample


def shared_fixture():
    design=sample();m=design['modules']['top'];layout=dict(clusters={},fixed_io={})
    for i,n in enumerate(('a','b','root')):layout['clusters'][n]=dict(lut=n,ff=None,row=49,column=89 if n=='root' else 90,slot=0 if n=='root' else i)
    for s in range(4,8):
        name=f'extra{s}';m['cells'][name]=dict(type='LUT1',parameters={'INIT':'10'},connections=dict(I0=[2],F=[100+s]),port_directions=dict(I0='input',F='output'))
        m['ports'][name]=dict(direction='output',bits=[100+s]);layout['clusters'][name]=dict(lut=name,ff=None,row=49,column=89,slot=s)
    cut=Cuts(design).function('root',frozenset(range(2,8)));after,_=replace(design,cut)
    return design,after,layout


def test_shared_macro_keeps_protected_ordinary_luts_and_compiles_all_groups(tmp_path):
    before,after,layout=shared_fixture();device=Device();fixed=copy.deepcopy(layout);cost=lambda r,c:100*(abs(r-49)+abs(c-89));protected={f'extra{s}' for s in range(4,8)}
    proposed,site,legal=clear_site(before,layout,'root',device,cost,protected,wide_leaf_slots=(0,1,2,3))
    assert site==(49,89) and legal['displaced_units']==0 and set(legal['preserved_ordinary_luts'])==protected
    placed,change=place(after,proposed,'root',device,site_cost=lambda r,c:0 if (r,c)==site else float('inf'),share_ordinary=True)
    assert change['shared_ordinary_luts'] and layout==fixed
    assert all(placed['clusters'][n]==layout['clusters'][n] for n in protected)
    check=validate(after,placed,device);assert set(check['macros'][0]['ordinary_luts'])==protected
    compiled=compile_wide(after,placed,device);assert [g[0] for g in compiled['sites'][0]['groups']]==[9,9,9,9]
    from ownpr_ff_configuration import independent_ff_groups
    expected=independent_ff_groups([])
    for group in expected:group[0]=9
    assert compiled['sites'][0]['groups']==expected
    from native_boundary_cec import prove
    assert all(r['cec']['equivalent'] for r in prove(before,after,tmp_path/'proof',30))


def test_shared_mode_rejects_unadmitted_occupied_leaf_and_ff():
    _,after,layout=shared_fixture();device=Device();placed,_=place(after,layout,'root',device,site_cost=lambda r,c:0 if (r,c)==(49,89) else float('inf'),share_ordinary=True)
    bad=copy.deepcopy(placed);bad.pop('mixed_wide_lut_calibration')
    with pytest.raises(ValueError,match='admitted'):validate(after,bad,device)
    bad=copy.deepcopy(placed);bad['clusters']['extra4']['slot']=0
    with pytest.raises(ValueError,match='Conflicting'):validate(after,bad,device)
    bad=copy.deepcopy(placed);bad['clusters']['extra4']['ff']='unsupported_ff'
    with pytest.raises(ValueError,match='FF sharing'):validate(after,bad,device)


def test_partial_clear_does_not_move_a_protected_leaf_slot_occupant():
    before,_,layout=shared_fixture();device=Device();layout['clusters']['extra4']['slot']=1
    _,site,report=clear_site(before,layout,'root',device,lambda r,c:100*(abs(r-49)+abs(c-89)),{'extra4'},wide_leaf_slots=(0,1))
    assert site!=(49,89)
    assert all(m['lut']!='extra4' for m in report['moves'])
