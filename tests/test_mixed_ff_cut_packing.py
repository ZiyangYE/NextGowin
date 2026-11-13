import copy,sys
from pathlib import Path
import pytest
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from ownpr_image_storage import load_json
from ownpr_device import Device
from ownpr_multilevel_cut import replace
from ownpr_macro_legalize import clear_site
from ownpr_timing_wide_remap import place
from ownpr_mux_macros import validate
from ownpr_wide_ff_lut_sharing import ADMISSION_SHA256


def test_registered_cut_keeps_spare_ordinary_luts_and_requires_admission():
    base=ROOT/'results/vexriscv_output_state_cache_replica_place_001'
    d=load_json(base/'packed.json');p=load_json(base/'placement.json');root='ng_coopt_3072';dev=Device()
    row=next(x for x in load_json(ROOT/'results/vexriscv_84_6_joint_cut_plan_002/plan.json')['chosen'] if x['cut']['root']==root)
    target=(p['clusters'][root]['row'],p['clusters'][root]['column']);cost=lambda r,c:0. if (r,c)==target else float('inf')
    with pytest.raises(ValueError,match='partial'):clear_site(d,p,root,dev,cost,wide_leaf_slots=(0,1))
    cleared,site,log=clear_site(d,p,root,dev,cost,wide_leaf_slots=(0,1),allow_paired_sharing=True)
    new,_=replace(d,row['cut'],row['late_bit'])
    after,change=place(new,cleared,root,dev,allow_paired=True,site_cost=cost,share_ordinary=True,share_paired=True)
    assert site==target and change['shared_paired_ff'] and log['preserved_ordinary_luts']
    for name in log['preserved_ordinary_luts']:assert after['clusters'][name]==p['clusters'][name]
    assert after['fixed_io']==p['fixed_io'] and after['ioreg_clusters']==p['ioreg_clusters']
    assert after['mixed_wide_ff_lut_calibration']==ADMISSION_SHA256
    validate(new,after,dev)
    bad=copy.deepcopy(after);bad.pop('mixed_wide_ff_lut_calibration')
    with pytest.raises(ValueError):validate(new,bad,dev)
    bad=copy.deepcopy(d);ff=p['clusters'][root]['ff'];bad['modules']['top']['cells'][ff]['connections']['RESET']=['1']
    with pytest.raises(ValueError,match='Uncalibrated'):clear_site(bad,p,root,dev,cost,wide_leaf_slots=(0,1),allow_paired_sharing=True)
