"""Partition a validated dedicated-clock candidate from ordinary routing."""
from ownpr_clock_graph import ClockGraph
from ownpr_device import ROOT


def partition(graph,nets,tree,clock_model=None):
    clocks=[n for n in nets if n.get('clock')]
    if len(clocks)!=1 or tree.get('clock_policy')!='AUTO':raise ValueError('Dedicated partition requires one AUTO clock')
    def point(node):
        r,c=graph[node];return (r+1,c+1,node%graph.n)
    clock=clocks[0]
    if tuple(tree['source'])!=point(clock['source']):raise ValueError('Dedicated clock source differs from bound I/O')
    if {tuple(p[0]) for p in tree['paths_sink_to_source']}!={point(n) for n in clock['sinks']}:raise ValueError('Dedicated clock endpoint set differs from placement')
    if any(not graph.device.names[point(n)[2]].startswith('CLK') for n in clock['sinks']):raise ValueError('Initial dedicated clock partition requires FF-clock-only loads')
    derived={}
    for path in tree['paths_sink_to_source']:
        points=[tuple(p) for p in path]
        if points[-1]!=tuple(tree['source']) or len(set(points))!=len(points):raise ValueError('Invalid clock source/acyclic path')
        for i,node in enumerate(points):
            parent=points[i+1] if i+1<len(points) else None
            if node in derived and derived[node]!=parent:raise ValueError('Conflicting clock parent choices')
            derived[node]=parent
    declared={tuple(r['node']):tuple(r['parent']) if r['parent'] else None for r in tree['parents']}
    if len(declared)!=len(tree['parents']) or derived!=declared:raise ValueError('Clock parent table differs from audited paths')
    model=clock_model if clock_model is not None else ClockGraph.for_tree(tree)
    if tree['topology_sha256']!=model.sha256 or model.audit_paths(tree['paths_sink_to_source'])['status']!='clock_paths_match':raise ValueError('Clock candidate contains non-native edges')
    return [n for n in nets if n is not clock],dict(net=clock['name'],sinks=len(clock['sinks']),configuration_verified=False,
        connectivity_pattern_checked=True,timing_signoff=False)
