import json,shutil,sys
from pathlib import Path
import pytest
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from ownpr_device import Device
from ownpr_clock_graph import ClockGraph
from ownpr_clock_configuration import ClockChoices
from ownpr_clock_timing_search import make_tree
from audit_native_clock_encoding import audit


@pytest.mark.parametrize('folder,bits,changes',[
    ('native_clock_bs1_prefix_encoding_001',7,2),('native_clock_bs1_encoding_001',34,7)])
def test_bs1_prefix_and_complete_path_match_compacted_native_capture(folder,bits,changes):
    p=ROOT/'results'/folder
    assert not (p/'encoding_baseline.fs').exists() and (p/'encoding_baseline.fs.gz').is_file()
    r=audit(p,p/'clock_tree.json')
    assert r['status']=='clock_candidate_bits_matched' and r['required_bits']==bits and r['actual_changes']==changes
    assert r['missing']==r['unexpected']==0 and r['full_payload_restored'] and not r['full_clock_legality_verified']


def test_bs1_cannot_drive_p33_alias_while_competing_primary_is_enabled():
    graph=ClockGraph(ROOT/'results/native_clock_inventory_005');choices=ClockChoices(Device(),graph)
    base=json.loads((ROOT/'results/ownpr_clock_bs1_candidate_001/clock_tree.json').read_text())
    path=tuple(graph.by_coordinate[tuple(p)] for p in reversed(base['paths_sink_to_source'][0]))
    primary=tuple(graph.by_coordinate[p] for p in ((85,182,38),(-2,-1,132),(-2,-1,19)))
    _,check=choices.requirements(make_tree(graph,base['source'],[path,primary]))
    assert check['conflicts']
    assert not choices.secondary_bottom_alias((-2,-1,262),(-2,-1,298))
    with pytest.raises(ValueError,match='Unimplemented'):choices.edge((-2,-1,132),(-1,-1,42))


def test_compacted_capture_still_requires_original_decoded_hash(tmp_path):
    original=ROOT/'results/native_clock_bs1_prefix_encoding_001'
    for name in ('manifest.json','encoding_audit.json','clock_tree.json','encoding_baseline.fs.gz','encoding_clock.fs.gz','encoding_restored.fs.gz'):
        shutil.copyfile(original/name,tmp_path/name)
    data=json.loads((tmp_path/'encoding_audit.json').read_text());data['baseline_sha256']='0'*64
    (tmp_path/'encoding_audit.json').write_text(json.dumps(data))
    with pytest.raises(ValueError,match='Clock encoding image changed'):audit(tmp_path,tmp_path/'clock_tree.json')


def test_full_bs1_clock_change_preserves_data_and_passes_final_global_acceptance():
    p=ROOT/'results/ownpr_uart_bs1_closure_001';meta=json.loads((p/'manifest.json').read_text());before=Path(meta['initial']['routes'])
    assert meta['status']=='bounded_clock_attempt_complete' and meta['wall_seconds']<600
    assert meta['evaluations'][0]['decision']=='accepted' and meta['metrics']['hold_violations']==0
    assert meta['metrics']['setup_wns_ns']>meta['initial_metrics']['setup_wns_ns'] and meta['metrics']['other_checks_passed']
    for name in ('routes.json','nets.json','all_nets.json','static_connections.json'):assert (p/'route'/name).read_bytes()==(before/name).read_bytes()
    assert not (p/'clock_encoding/encoding_baseline.fs').exists()
    result=audit(p/'clock_encoding',p/'route/clock_tree.json')
    assert result['status']=='clock_candidate_bits_matched' and result['required_bits']==905 and result['actual_changes']==237
    assert not meta['timing_signoff'] and not meta['full_bitstream_verified']
