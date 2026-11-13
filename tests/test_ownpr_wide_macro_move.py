import copy, json, sys
from pathlib import Path
import pytest
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from ownpr_wide_macro_move import relocate,diversify
from ownpr_joint_feedback import plan
from ownpr_device import Device


@pytest.fixture(scope='module')
def source():
    folder=ROOT/'results/ownpr_uart_ce_selector_closure_001/round_003/placement'
    return json.loads((folder/'packed.json').read_text()),json.loads((folder/'placement.json').read_text()),Device()


def test_move_retains_logic_pins_register_sites_and_macro_slots(source):
    design,placed,device=source;before=copy.deepcopy(design)
    changed,info=relocate(design,placed,device,'ng_acd_114',102,159)
    assert design==before and changed['fixed_io']==placed['fixed_io']
    assert changed['ioreg_clusters']==placed['ioreg_clusters'] and changed['arithmetic_clusters']==placed['arithmetic_clusters']
    for name,row in placed['clusters'].items():
        if name in info['moved_luts']:
            assert changed['clusters'][name]==dict(row,row=102,column=159)
        else:assert changed['clusters'][name]==row
    for name,row in placed['mux_clusters'].items():
        if name in info['moved_muxes']:
            assert changed['mux_clusters'][name]==dict(row,row=102,column=159)
        else:assert changed['mux_clusters'][name]==row
    assert info['logical_netlist_byte_identical'] and placed['mux_clusters']['ng_acd_114']['column']==155


def test_move_rejects_occupied_uncalibrated_and_unchanged_sites(source):
    design,placed,device=source
    for row,column in [(102,155),(102,147),(-1,159),(0,0)]:
        with pytest.raises(ValueError):relocate(design,placed,device,'ng_acd_114',row,column)


def test_audited_feedback_proposes_executable_area_neutral_move(source):
    folder=ROOT/'results/ownpr_uart_ce_selector_closure_001/round_003/placement'
    run=ROOT/'results/ownpr_uart_multipath_closure_001/round_001'
    result=plan(folder,run/'eco',run/'eco_export/native',max_candidates=200,max_growth=0,include_moves=True)
    choices=[p for p in result['candidates'] if p['kind']=='wide_move']
    assert choices and choices[0]['root']=='ng_acd_114' and choices[0]['estimated_local_gain_ns']>0
    assert all(p['lut_growth']==0 and 1 in p['witness_paths'] for p in choices[:3])
    design,placed,device=source
    for p in choices:relocate(design,placed,device,p['root'],p['row'],p['column'])
    assert any(p.endswith('sta_endpoint_audit.json') for p in result['source_sha256'])
    diverse=diversify(choices)
    assert diverse[0]['root']=='ng_acd_114' and diverse[1]['root']=='ng_acd_146'


def test_real_moves_keep_245_trees_frozen_and_reject_global_regression():
    for name in ('ownpr_uart_macro_move_closure_001','ownpr_uart_macro_move_closure_002'):
        folder=ROOT/'results'/name; state=json.loads((folder/'manifest.json').read_text())
        assert state['status']=='bounded_rounds_complete' and state['selected']==state['initial']
        entry=state['rounds'][0]
        assert entry['logical_netlist_byte_identical'] and not entry['proof']
        assert all(e['decision']=='rejected_global_timing' for e in entry['evaluations'])
        old=Path(entry['source']['placement']);new=folder/'round_001/placement'
        assert (old/'packed.json').read_bytes()==(new/'packed.json').read_bytes()
        before=json.loads((Path(entry['source']['routes'])/'routes.json').read_text())['nets']
        after=json.loads((folder/'round_001/route/routes.json').read_text())['nets']
        assert sum(after[n]==r for n,r in before.items())==245
        assert (folder/'round_001/route/clock_tree.json').read_bytes()==(Path(entry['source']['routes'])/'clock_tree.json').read_bytes()
