import copy,json,sys
import pytest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from ownpr_ioreg_timing import IoregTimingModel

def test_sync_constant_calibration_coexists_with_preset_model():
    model=IoregTimingModel();model.ensure_constant_sync_calibration();model.ensure_preset_calibration()
    assert model.constant_sync_sha256 and model.preset_sha256
    assert ('RESET',1,True) in model.values and ('CLEAR',1,True) in model.values


def test_constant_sync_packing_rejects_dynamic_reset_and_disabled_ce():
    from ownpr_ioreg_macros import validate,reserved_points
    root=Path(__file__).resolve().parents[1]
    folder=root/'results/int2float_constant_ioreg_preflight_001'
    design=json.loads((folder/'packed.json').read_text())
    placed=json.loads((folder/'placement.json').read_text())
    record=validate(design,placed)['records'][0]
    assert record['control_mode']=='zero' and record['ce_mode']=='one'
    assert {tuple(record['pins'][p]) for p in ('RESET','CE')}<=reserved_points(design,placed)
    for pin,value in [('RESET',None),('CE','0')]:
        bad=copy.deepcopy(design)
        cell=bad['modules']['top']['cells'][record['instance']]
        cell['connections'][pin]=cell['connections']['D'] if value is None else [value]
        with pytest.raises(ValueError,match='requires CE=1 and RESET=0'):
            validate(bad,placed)
