import json, sys
from pathlib import Path
import pytest
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from ownpr_eco_feedback import setup_paths, SetupProgress


def paths():
    return [dict(path=1, slack_ns=-.2, arcs=[dict(label='shared', transition='R', delay_ns=.8),
        dict(label='first', transition='F', delay_ns=.2)]),
        dict(path=2, slack_ns=-.15, arcs=[dict(label='shared', transition='F', delay_ns=.8),
        dict(label='second', transition='R', delay_ns=.4)])]


def resolve(label):
    return {'shared': ('a', 1), 'first': ('b', 2), 'second': ('a', 3)}[label]


def test_path_gain_is_not_shared_between_unrelated_paths():
    p = SetupProgress(paths(), resolve, 2)
    # Fixing only the first path must not stop the second path's search.
    p.update('b', {2: (1., .1, 1., 1.)}, {2: (.7, .1, .7, .7)})
    first = next(t for t in p.targets if t['net'] == 'b')
    second = next(t for t in p.targets if t['sink'] == 3)
    assert not p.pending(first) and p.pending(second)
    assert p.gains == pytest.approx({1: .3, 2: 0.})


def test_shared_wire_uses_both_transitions_and_tracks_other_sinks():
    p = SetupProgress(paths(), resolve, 2)
    shared = next(t for t in p.targets if t['sink'] == 1)
    before = {1: (1., .1, 1., .8), 3: (1., .1, 1., .9)}
    after = {1: (.9, .1, .7, .9), 3: (1.05, .1, 1.05, .9)}
    assert p.gain(shared, before[1], after[1]) == pytest.approx(-.1)
    p.update('a', before, after)
    assert p.gains == pytest.approx({1: .3, 2: -.15})
    assert len(p.targets) == 3 and shared['witness_paths'] == [1, 2]


def test_real_report_coverage_and_falling_wire_paths():
    folder = ROOT / 'results/ownpr_uart_mixed_closure_001/round_002/eco_export/native'
    audit = json.loads((folder / 'handoff_audit.json').read_text())
    count = audit['timing']['path_coverage']['setup']['paths']
    text = (folder / 'probe.tr').read_text()
    parsed = setup_paths(text, count)
    assert len(parsed) == 25 and parsed[0]['slack_ns'] == -.196
    assert any(a['transition'] == 'F' for a in parsed[0]['arcs'])
    assert not any(a['label'].endswith('/CLK') for p in parsed for a in p['arcs'])
    with pytest.raises(ValueError, match='coverage'):
        setup_paths(text, count + 1)
    with pytest.raises(ValueError, match='Incomplete setup trace'):
        setup_paths(text.replace('Data Arrival Path:', 'Missing Arrival Path:', 1), count)


def test_limit_and_duplicate_edges_do_not_double_count():
    rows = paths()
    rows[0]['arcs'].append(dict(rows[0]['arcs'][0]))
    p = SetupProgress(rows, resolve, 1)
    p.update('a', {1: (1., .1, 1., 1.)}, {1: (.9, .1, .9, .9)})
    assert p.gains == pytest.approx({1: .1}) and len(p.targets) == 2
    with pytest.raises(ValueError, match='limit'):
        SetupProgress(rows, resolve, 0)


def test_route_only_rejects_netlist_or_placement_mutation(tmp_path):
    from ownpr_place import sha
    from run_ownpr_joint_closure import check_unchanged_placement
    (tmp_path / 'packed.json').write_text('original netlist')
    (tmp_path / 'placement.json').write_text('original placement')
    meta = dict(packed_sha256=sha(tmp_path / 'packed.json'), placement_sha256=sha(tmp_path / 'placement.json'))
    (tmp_path / 'manifest.json').write_text(json.dumps(meta))
    check_unchanged_placement(tmp_path, tmp_path)
    for filename in ('packed.json', 'placement.json'):
        file = tmp_path / filename
        original = file.read_text()
        file.write_text(original + ' changed')
        with pytest.raises(ValueError, match='Route-only ECO changed'):
            check_unchanged_placement(tmp_path, tmp_path)
        file.write_text(original)


def test_completed_multipath_flow_preserves_contract_and_improves_secondary_paths():
    from run_ownpr_joint_closure import check_unchanged_placement
    folder = ROOT / 'results/ownpr_uart_multipath_closure_001'
    result = json.loads((folder / 'manifest.json').read_text())
    assert result['status'] == 'bounded_rounds_complete' and result['route_only']
    entry = result['rounds'][0]
    assert entry['status'] == 'accepted' and entry['logical_netlist_byte_identical'] and not entry['proof']
    check_unchanged_placement(entry['source']['sta'], result['selected']['placement'])
    assert result['metrics']['setup_wns_ns'] == result['initial_metrics']['setup_wns_ns']
    assert result['metrics']['setup_violations'] < result['initial_metrics']['setup_violations']
    assert result['metrics']['hold_violations'] == 0
    eco = json.loads((folder / 'round_001/eco/manifest.json').read_text())
    assert eco['setup_path_progress']['selected_paths'] == 8
    assert len(eco['eco_changes']) == 6 and all(len(c['before']) == 4 for c in eco['eco_changes'])
    assert eco['setup_path_progress']['paths'][0]['estimated_wire_gain_ns'] == 0
