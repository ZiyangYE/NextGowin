import copy,sys
from pathlib import Path
import pytest
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from ownpr_image_storage import load_json
from prepare_wide_ff_lut_neighbor import transform
from ownpr_mux_macros import validate
from ownpr_device import Device


def test_real_cpu_mixed_slot_preserves_state_and_rejects_unadmitted_mode():
    base=ROOT/'results/vexriscv_competing_multilevel_001/candidate';d=load_json(base/'packed.json');p=load_json(base/'placement.json');original=copy.deepcopy(d)
    moved,record,audit=transform(d,p,'ng_coopt_1008','ng_ownpr_region_5_1',7)
    assert d==original and moved['fixed_io']==p['fixed_io']
    assert [n for n in p['clusters'] if p['clusters'][n]!=moved['clusters'][n]]==['ng_coopt_1008']
    assert record['new_site']==[69,92,7] and audit['capacity_verified']
    bad=copy.deepcopy(moved);bad.pop('mixed_wide_ff_lut_calibration')
    with pytest.raises(ValueError,match='admission'):validate(d,bad,Device())
    bad=copy.deepcopy(d);bad['modules']['top']['cells']['ng_ownpr_region_5_0']['connections']['RESET']=['1']
    with pytest.raises(ValueError):validate(bad,moved,Device())
    with pytest.raises(ValueError,match='occupied'):transform(d,p,'ng_coopt_1008','ng_ownpr_region_5_1',0)
