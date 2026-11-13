import copy, json, sys, time
from pathlib import Path
import pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from ownpr_whole_net_eco import rebuild


class Graph:
    def __init__(self):
        self.coordinates = [(0, 0)] * 7
        self.heuristic_scale = 0.
        self.root_arrival_weight = .6
        self.edges = {(0, 1): 2., (1, 2): 2., (1, 3): 2.,
            (0, 4): .1, (4, 2): .1, (4, 3): .1,
            (0, 5): .01, (5, 2): .01, (5, 3): .01}
    def uphill(self, node):
        return [(a, cost, (0, 0, str(a), str(b))) for (a, b), cost in self.edges.items() if b == node]
    def node(self, r, c, name): return int(name)
    def check_pip(self, pip): return tuple(map(int, pip[2:])) in self.edges


def test_rebuild_replaces_shared_trunk_and_keeps_other_resources_frozen():
    graph = Graph()
    net = dict(name='critical', source=0, sinks=[2, 3], criticality=.5)
    before = copy.deepcopy(net)
    owners = {0: 'critical', 1: 'critical', 2: 'critical', 3: 'critical', 5: '__clock__'}
    rows, info = rebuild(graph, net, 3, owners, time.monotonic() + 5)
    assert rows and info['candidates'] == len(rows)
    assert all(4 in row['nodes'] and 1 not in row['nodes'] and 5 not in row['nodes'] for row in rows)
    assert net == before and graph.root_arrival_weight == .6
    assert all(t['legality']['connectivity_verified'] for t in info['trials'])


def test_timeout_and_unsupported_modes_keep_original_state():
    graph = Graph(); net = dict(name='critical', source=0, sinks=[2, 3])
    rows, info = rebuild(graph, net, 2, {}, time.monotonic() - 1)
    assert not rows and info['trials'][0]['status'] == 'budget_exhausted'
    rows, info = rebuild(graph, dict(net, clock=True), 2, {}, time.monotonic() + 5)
    assert not rows and info['status'] == 'unsupported_clock_or_constant'
    assert graph.root_arrival_weight == .6
    with pytest.raises(ValueError, match='original net'):
        rebuild(graph, net, 6, {}, time.monotonic() + 5)


def test_frozen_endpoint_is_rejected_and_search_settings_restored():
    graph = Graph(); net = dict(name='critical', source=0, sinks=[2, 3])
    with pytest.raises(ValueError, match='reserved'):
        rebuild(graph, net, 2, {2: 'other'}, time.monotonic() + 5)
    assert graph.root_arrival_weight == .6


def test_real_whole_net_trials_reject_nonimproving_loaded_tree_delays():
    root=Path(__file__).resolve().parents[1]
    folder=root/'results/ownpr_uart_whole_net_closure_001'
    state=json.loads((folder/'manifest.json').read_text())
    eco=json.loads((folder/'round_001/eco/manifest.json').read_text())
    trial=eco['whole_net_searches'][0]
    assert trial['net']=='net_154' and trial['candidates']==2 and trial['frozen_resources']==2002
    assert all(t['legality']['connectivity_verified'] for t in trial['trials'])
    assert all(t['status']=='local_timing_rejected' for t in trial['evaluations'])
    assert trial['evaluations'][0]['target_gain_ns']==0 and trial['evaluations'][1]['target_gain_ns']<0
    assert state['selected']==state['initial'] and state['metrics']==state['initial_metrics']
