"""Per-path setup budgets for bounded routing ECO, using complete STA traces.

Changes in measured wire delay are estimates of path improvement: final STA
still has to account for cell slew/load effects and all unselected paths.
"""
import re
from extract_timing import ROW
from ownpr_timing_numbers import negative_slack


def setup_paths(text, expected_count):
    section = re.search(r'^3\.3\.1 Setup Analysis Report\n(.*?)(?=^3\.|\Z)', text, re.M | re.S)
    if not section:
        raise ValueError('Missing setup analysis for route ECO')
    blocks = re.split(r'^\s*Path(\d+)\s*$', section[1], flags=re.M)
    if (len(blocks) - 1) // 2 != expected_count:
        raise ValueError('Incomplete setup path coverage for route ECO')
    result = []
    ids = set()
    for i in range(1, len(blocks), 2):
        pid = int(blocks[i])
        if pid in ids:
            raise ValueError('Duplicate setup path identifier')
        ids.add(pid)
        body = blocks[i + 1]
        slack = re.search(r'^Slack\s*:\s*([-\d.]+)', body, re.M)
        trace = re.search(r'Data Arrival Path:\n(.*?)(?=\nData Required Path:)', body, re.S)
        if not slack or not trace:
            raise ValueError('Incomplete setup trace for route ECO')
        if not negative_slack(slack[1]):
            continue
        arcs = []
        for line in trace[1].splitlines():
            match = ROW.match(line)
            if not match:
                continue
            _, delay, kind, transition, _, _, label = match.groups()
            if kind == 'tNET' and not label.endswith('/CLK'):
                if transition not in ('RR', 'FF'):
                    raise ValueError('Unknown setup transition: ' + repr(transition))
                arcs.append(dict(label=label, transition=transition[-1], delay_ns=float(delay)))
        result.append(dict(path=pid, slack_ns=float(slack[1]), arcs=arcs))
    return sorted(result, key=lambda p: (p['slack_ns'], p['path']))


class SetupProgress:
    """Track each failing path separately; shared wires contribute once per path."""
    def __init__(self, paths, resolve, limit):
        if not 1 <= limit <= 100:
            raise ValueError('Setup path limit must be 1..100')
        self.paths = paths[:limit]
        self.gains = {p['path']: 0. for p in self.paths}
        self.required = {p['path']: -p['slack_ns'] + .020 for p in self.paths}
        targets = {}
        self.edges = {}
        for path in self.paths:
            edges = set()
            for arc in path['arcs']:
                endpoint = resolve(arc['label'])
                if endpoint is None:
                    continue
                net, sink = endpoint
                key = net, sink
                edge = net, sink, arc['transition']
                edges.add(edge)
                target = targets.setdefault(key, dict(net=net, sink=sink, kind='setup',
                    improvement=self.required[path['path']], witness_paths=[], transitions=[],
                    worst_slack_ns=path['slack_ns'], wire_delay_ns=arc['delay_ns']))
                target['wire_delay_ns'] = max(target['wire_delay_ns'], arc['delay_ns'])
                if path['path'] not in target['witness_paths']:
                    target['witness_paths'].append(path['path'])
                if arc['transition'] not in target['transitions']:
                    target['transitions'].append(arc['transition'])
            self.edges[path['path']] = edges
        self.targets = sorted(targets.values(), key=lambda t: (t['worst_slack_ns'], -t['wire_delay_ns'], t['net'], t['sink']))

    def pending(self, target):
        return any(self.gains[p] < self.required[p] for p in target['witness_paths'])

    @staticmethod
    def gain(target, before, after):
        # Whole-tree measurement tuples: max, min, rise-max, fall-max.
        return min(before[2 if t == 'R' else 3] - after[2 if t == 'R' else 3]
                   for t in target['transitions'])

    def update(self, net, before, after):
        # Updating every sink also charges side-branch regressions to its paths.
        for pid, edges in self.edges.items():
            for edge_net, sink, transition in edges:
                if edge_net != net:
                    continue
                index = 2 if transition == 'R' else 3
                self.gains[pid] += before[sink][index] - after[sink][index]

    def report(self):
        return dict(selected_paths=len(self.paths), unique_targets=len(self.targets),
            paths=[dict(path=p, required_wire_gain_ns=self.required[p], estimated_wire_gain_ns=self.gains[p])
                   for p in self.gains], scope='Wire-only per-path estimates; final full STA required')
