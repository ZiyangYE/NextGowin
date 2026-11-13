import copy,json,sys
from pathlib import Path
import pytest
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from ownpr_cluster_swap import swap
from ownpr_device import Device


@pytest.fixture(scope='module')
def fixture():
    p=ROOT/'results/ownpr_uart_cascade_pin_closure_001/round_002/eco/placement'
    return json.loads((p/'packed.json').read_text()),json.loads((p/'placement.json').read_text()),Device()


@pytest.mark.parametrize('partner',['ng_acd_155','ng_acd_166'])
def test_pair_swap_preserves_clock_sites_logic_and_other_clusters_across_calibrated_types(fixture,partner):
    design,placed,device=fixture;old=copy.deepcopy(design);new,change=swap(design,placed,device,'ng_acd_160',partner)
    point=lambda c:(c['row'],c['column'],c['slot'])
    assert point(new['clusters']['ng_acd_160'])==point(placed['clusters'][partner])
    assert point(new['clusters'][partner])==point(placed['clusters']['ng_acd_160'])
    assert {point(c) for c in new['clusters'].values() if c['ff']}=={point(c) for c in placed['clusters'].values() if c['ff']}
    assert all(new['clusters'][n]==c for n,c in placed['clusters'].items() if n not in ('ng_acd_160',partner))
    assert new['fixed_io']==placed['fixed_io'] and design==old and 228 in change['calibrated_tile_types']


def test_pair_swap_rejects_unpaired_modes_clock_domains_and_nondefault_init(fixture):
    design,placed,device=fixture
    with pytest.raises(ValueError,match='paired'):swap(design,placed,device,'ng_acd_160','ng_acd_2')
    with pytest.raises(ValueError,match='different clusters'):swap(design,placed,device,'ng_acd_160','ng_acd_160')
    changed=copy.deepcopy(design);ff=placed['clusters']['ng_acd_155']['ff'];changed['modules']['top']['cells'][ff]['connections']['CLK']=[999999]
    with pytest.raises(ValueError,match='clock domains'):swap(changed,placed,device,'ng_acd_160','ng_acd_155')
    changed=copy.deepcopy(design);changed['modules']['top']['cells'][ff]['parameters']['INIT']='1'
    with pytest.raises(ValueError,match='Nondefault'):swap(changed,placed,device,'ng_acd_160','ng_acd_155')


def test_pair_swap_rejects_overlap_and_unverified_ff_slot(fixture):
    design,placed,device=fixture;bad=copy.deepcopy(placed);bad['clusters']['ng_acd_160']['slot']=6
    with pytest.raises(ValueError,match='Uncalibrated'):swap(design,bad,device,'ng_acd_160','ng_acd_155')
    bad=copy.deepcopy(placed);bad['clusters']['ng_acd_1'].update({k:placed['clusters']['ng_acd_160'][k] for k in ('row','column','slot')})
    with pytest.raises(ValueError,match='Overlapping'):swap(design,bad,device,'ng_acd_160','ng_acd_155')


def test_complete_swap_trials_keep_fixed_clock_logic_and_reject_global_regression():
    base=ROOT/'results/ownpr_uart_cascade_pin_closure_001/round_002/eco'
    for run,decision in (('002','rejected_global_timing'),('003','accepted')):
        p=ROOT/('results/ownpr_uart_cluster_swap_'+run);r=json.loads((p/'manifest.json').read_text());e=r['evaluations'][0]
        assert r['status']=='bounded_cluster_attempt_complete' and r['wall_seconds']<600 and e['decision']==decision
        assert (p/'route/clock_tree.json').read_bytes()==(base/'clock_tree.json').read_bytes()
        assert (p/'placement/packed.json').read_bytes()==(base/'placement/packed.json').read_bytes()
        assert e['metrics']['hold_violations']==0 and e['metrics']['other_checks_passed']
        assert (e['metrics']['setup_wns_ns']>r['initial_metrics']['setup_wns_ns'])==(decision=='accepted')
        assert not r['timing_signoff'] and not r['full_bitstream_verified']
