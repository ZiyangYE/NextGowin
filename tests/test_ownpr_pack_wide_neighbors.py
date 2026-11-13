import copy,sys
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from ownpr_pack_wide_neighbors import propose
from ownpr_timing_wide_remap import place
from ownpr_mux_macros import validate
from ownpr_device import Device
from test_ownpr_wide_lut_sharing import shared_fixture


def fixture():
    _,d,p=shared_fixture()
    for i in range(4,8):p['clusters'][f'extra{i}'].update(column=91,slot=i-4)
    p,_=place(d,p,'root',Device(),site_cost=lambda r,c:0 if (r,c)==(49,89) else float('inf'))
    d['modules']['top']['cells']['extra4']['connections']['I0']=[12]
    ranking=dict(ranked_nets=[dict(driver=dict(instance='root'),endpoints=['extra4/I0'],worst_path_slack_ns=.1,maximum_reported_wire_ns=2.)])
    return d,p,ranking


def test_existing_lut6_spare_slot_accepts_an_ordinary_critical_neighbor():
    d,p,r=fixture();before=copy.deepcopy((d,p));placed,selected=propose(d,p,r,{('extra4','I0'):1.},6,1)
    assert selected[0]['new_site']==[49,89,4] and selected[0]['weighted_span_after']==0
    assert (d,p)==before and placed['fixed_io']==p['fixed_io']
    result=validate(d,placed,Device());assert result['macros'][0]['ordinary_luts']==['extra4']


def test_neighbor_packing_never_moves_a_paired_ff_or_overbooks_capacity():
    d,p,r=fixture();p['clusters']['extra4']['ff']='protected_state'
    with pytest.raises(ValueError,match='No reported'):propose(d,p,r,{},6,1)
    d,p,r=fixture()
    for i in range(4,8):p['clusters'][f'extra{i}'].update(column=89,slot=i)
    with pytest.raises(ValueError,match='No reported'):propose(d,p,r,{},6,1)


def test_only_calibrated_levels_are_admitted():
    d,p,r=fixture()
    with pytest.raises(ValueError,match='scope'):propose(d,p,r,{},7,1)
