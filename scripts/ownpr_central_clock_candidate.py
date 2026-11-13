"""Own BFS within a calibrated PCLKB0/central-P30 clock resource profile.

Only source and endpoints are reused. No vendor placement or routed tree is
copied. Complete source-mode, clock capacity and STA gates remain mandatory.
"""
import argparse,json
from collections import defaultdict
from pathlib import Path
from ownpr_clock_graph import ClockGraph


def candidate(graph,source,sinks):
    source=tuple(source)
    allowed={(source,(-2,-1,133)),((-2,-1,133),(-1,-1,16)),
        ((-1,-1,16),(-1,-1,297)),((-1,-1,297),(-1,-2,245)),((-1,-1,297),(-2,-1,245))}
    for group in [(-1,-2),(-2,-1)]:
        for alias in [286,292,298,304]:allowed.add((group+(245,),group+(alias,)))
    for a,b in allowed:
        if a not in graph.by_coordinate or b not in graph.by_coordinate or graph.by_coordinate[b] not in graph.adj[graph.by_coordinate[a]]:
            raise ValueError('Clock inventory does not support the selected central profile')
    original=graph.adj;restricted=defaultdict(list)
    for a,targets in original.items():
        before=tuple(graph.nodes[a]['coordinate'])
        restricted[a]=[b for b in targets if min(graph.nodes[b]['coordinate'][:2])>=1 or (before,tuple(graph.nodes[b]['coordinate'])) in allowed]
    try:
        graph.adj=restricted;tree=graph.candidate(source,sinks,'AUTO')
    finally:graph.adj=original
    if graph.audit_paths(tree['paths_sink_to_source'])['status']!='clock_paths_match':raise ValueError('Clock candidate left the original resource graph')
    tree.update(global_profile='PCLKB0-centralP30-PCLKBOT0-direct_regional_aliases',candidate_only=True)
    return tree


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--inventory',type=Path,required=True);p.add_argument('--endpoints',type=Path,required=True);p.add_argument('--out',type=Path,required=True);a=p.parse_args()
    from workspace_budget import reserve
    reserve(Path(__file__).resolve().parents[1],10_000_000)
    previous=json.loads(a.endpoints.read_text());tree=candidate(ClockGraph(a.inventory),previous['source'],[p[0] for p in previous['paths_sink_to_source']])
    with a.out.open('x') as f:json.dump(tree,f,indent=2)
