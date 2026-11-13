import copy,sys
from pathlib import Path
import pytest
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from ownpr_timing_wide_remap import late_boundary,transform,place
from ownpr_image_storage import load_json
from test_ownpr_selector_cascade import evaluate


def test_real_cpu_cut_promotes_reported_late_input_and_matches_every_assignment():
    d=load_json(ROOT/'results/vexriscv_timing_cut_place_001/packed.json');before=copy.deepcopy(d)
    traces=[dict(slack_ns=.052,ordinary_wires=[dict(endpoint='ng_coopt_268/I1'),dict(endpoint='ng_coopt_1106/I1')])]
    late=late_boundary(d,'ng_coopt_268','ng_coopt_1106',traces,.052)
    after,info=transform(d,'ng_coopt_268','ng_coopt_1106',late)
    assert d==before and late==3642 and info['cut_width']==6 and info['lut_growth']==3
    assert after['modules']['top']['cells']['ng_coopt_1106']['connections']['S0']==[late]
    for a in range(64):
        values={b:a>>i&1 for i,b in enumerate(info['cut_bits'])}
        assert evaluate(d,'ng_coopt_1106',values)==evaluate(after,'ng_coopt_1106',values)
    with pytest.raises(ValueError,match='preceding'):late_boundary(d,'ng_coopt_268','ng_coopt_1106',[],.052)
    with pytest.raises(ValueError,match='boundary'):transform(d,'ng_coopt_268','ng_coopt_1106',9999999)


def test_wide_placement_preserves_state_hard_io_and_uses_empty_cfu():
    from ownpr_device import Device
    d=load_json(ROOT/'results/vexriscv_timing_cut_place_001/packed.json');p=load_json(ROOT/'results/vexriscv_timing_cut_place_001/placement.json')
    after,_=transform(d,'ng_coopt_268','ng_coopt_1106',3642);placed,info=place(after,p,'ng_coopt_1106',Device())
    assert all(placed[k]==p[k] for k in ('fixed_io','hard_clusters','arithmetic_clusters'))
    assert all(placed['clusters'][n]==r for n,r in p['clusters'].items() if n!='ng_coopt_1106')
    assert info['new_cfu'] not in [[r['row'],r['column']] for r in p['clusters'].values()]
