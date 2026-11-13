import copy,sys
from pathlib import Path
import pytest
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from ownpr_critical_chain_move import rank,apply
from ownpr_device import Device
from ownpr_image_storage import load_json,read_text


@pytest.fixture(scope='module')
def candidate():
    base=ROOT/'results/vexriscv_batch_hold_003/round_000/placement';sta=ROOT/'results/vexriscv_48_5mhz_001/export/native'
    d=load_json(base/'packed.json');p=load_json(base/'placement.json');device=Device()
    rows=rank(d,p,device,read_text(sta/'probe.tr'),load_json(sta/'name_map.json'))
    return d,p,device,rows[0]


def test_real_critical_chain_legalizes_without_moving_state_or_changing_logic(candidate):
    d,p,dev,row=candidate;before=copy.deepcopy(d);layout=apply(d,p,dev,row)
    assert d==before and row['worst_slack_ns']==.023 and row['distance_gain']>0
    assert len(row['members'])>=2 and row['displaced']
    assert all(not p['clusters'][n]['ff'] for n in row['sites'])
    for n,old in p['clusters'].items():
        if n not in row['sites']:assert layout['clusters'][n]==old
    for key in ('fixed_io','hard_clusters','arithmetic_clusters','mux_clusters'):assert layout.get(key)==p.get(key)
    assert {tuple((r['row'],r['column'],r['slot'])) for r in p['clusters'].values() if r.get('ff')}=={tuple((r['row'],r['column'],r['slot'])) for r in layout['clusters'].values() if r.get('ff')}
    sites=[(r['row'],r['column'],r['slot']) for r in layout['clusters'].values()]
    assert len(sites)==len(set(sites))


def test_paired_cluster_and_overlapping_destinations_are_rejected(candidate):
    d,p,dev,row=candidate;bad=copy.deepcopy(row)
    paired=next(n for n,r in p['clusters'].items() if r.get('ff'))
    bad['sites']={paired:[65,84,7]}
    with pytest.raises(ValueError,match='unpaired'):apply(d,p,dev,bad)
    bad=copy.deepcopy(row);names=list(bad['sites']);bad['sites'][names[1]]=bad['sites'][names[0]]
    with pytest.raises(ValueError,match='Overlapping'):apply(d,p,dev,bad)


def test_unreachable_grid_or_invalid_slot_is_rejected(candidate):
    d,p,dev,row=candidate;bad=copy.deepcopy(row);bad['sites'][next(iter(bad['sites']))]=[-1,1,0]
    with pytest.raises(ValueError,match='Uncalibrated'):apply(d,p,dev,bad)


def test_relative_criticality_covers_near_critical_positive_slack_paths(candidate):
    from ownpr_placement_feedback import load
    d,p,dev,row=candidate;sta=ROOT/'results/vexriscv_48_5mhz_001/export/native'
    weights,meta=load(sta,d,relative_window_ns=2.)
    assert meta['paths']>1 and meta['weighted_endpoints']>len(row['critical_edges'])
    assert max(weights.values())==1 and any(0<w<1 for w in weights.values())
    original,_=load(ROOT/'results/vexriscv_batch_hold_003/round_000/export/native',d,relative_window_ns=2.)
    assert set(original)==set(weights)
    assert max(abs(weights[k]-original[k]) for k in weights)<.001
    options=rank(d,p,dev,read_text(sta/'probe.tr'),load_json(sta/'name_map.json'),priorities=weights)
    assert options and options[0]['global_criticality_used'];apply(d,p,dev,options[0])


def test_real_tmds_wide_macro_leaf_cannot_be_moved_as_an_ordinary_lut():
    from ownpr_critical_chain_move import exclusive_tiles
    base=ROOT/'results/tmds_feedback_moves_002/round_003/placement'
    d=load_json(base/'packed.json');p=load_json(base/'placement.json');tiles=exclusive_tiles(p)
    name=next(n for n,r in p['clusters'].items() if (r['row'],r['column']) in tiles and not r.get('ff'))
    with pytest.raises(ValueError,match='exclusive macro'):apply(d,p,Device(),dict(sites={name:[60,60,1]}))
