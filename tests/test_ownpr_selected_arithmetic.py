import copy,sys
from pathlib import Path
import pytest
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from ownpr_image_storage import load_json
from select_ownpr_arithmetic import rank
from remap_ownpr_logic import boundary


def test_feedback_preserves_complete_nearest_carry_groups_only():
    d=load_json(ROOT/'results/coopt_tmds_initial_001/input.json');before=copy.deepcopy(d)
    rows=rank(d,[dict(instance='$auto$ff.cc:337:slice$1643',pin='D',slack_ns=.006)])
    selected=[r for r in rows if r['eligible']][:2];assert [r['length'] for r in selected]==[8,8]
    heads=[r['head'] for r in selected];chosen,_,_,info=boundary(d,'selected',heads)
    kept={n for r in selected for n in r['cells']};assert not chosen&kept and info['preserved_alu_count']==16 and info['soft_alu_count']==76
    assert d==before
    with pytest.raises(ValueError,match='unknown'):boundary(d,'selected',['nonexistent'])
    with pytest.raises(ValueError,match='Unknown'):boundary(d,'typo')


def test_anchored_wide_placement_preserves_all_ff_sites_and_io():
    from ownpr_place import pack,place,constraints
    from ownpr_device import Device
    from ownpr_mux_macros import tree,validate
    base=ROOT/'results/tmds_wide_replica_003/placement';original=load_json(base/'packed.json');old=load_json(base/'placement.json')
    d,g,c,_=pack(original,'top',allow_alu=True,allow_wide=True);dev=Device()
    contract=constraints(d['modules']['top'],(ROOT/'results/tangprimer138_suite_001/tmds/fixed.cst').read_text(),(ROOT/'results/tmds_102_6mhz_001/clock.sdc').read_text())
    anchors={r['ff']:[r['row'],r['column'],r['slot']] for r in old['clusters'].values() if r.get('ff')}
    wide=[tree(d,m['root'],m['root_slot']) for m in old['wide_macros']]
    new=place(d,'top',g,c,contract,dev,ROOT/'results/native_route_runtime_007',wide=wide,ff_sites=anchors)
    observed={r['ff']:[r['row'],r['column'],r['slot']] for r in new['clusters'].values() if r.get('ff')}
    assert observed==anchors and new['fixed_io']==old['fixed_io'];validate(d,new,dev)
    with pytest.raises(ValueError,match='cover every FF'):place(d,'top',g,c,contract,dev,ROOT/'results/native_route_runtime_007',wide=wide,ff_sites={})
