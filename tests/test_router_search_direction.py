import sys
from pathlib import Path
from collections import defaultdict
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from ownpr_router import connection_search,route,validate


class Graph:
    def __init__(self):
        self.coordinates=[(0,0)]*80;self.heuristic_scale=0.;self.root_arrival_weight=1.
        self.edges={(0,1):.1,(1,2):.1,**{(i,2):.1 for i in range(10,70)}}
    def node(self,r,c,w):return int(w)
    def uphill(self,n):return [(a,d,(0,0,str(a),str(b))) for (a,b),d in self.edges.items() if b==n]
    def downhill(self,n):return [(b,d,(0,0,str(a),str(b))) for (a,b),d in self.edges.items() if a==n]
    def check_pip(self,p):return tuple(map(int,p[2:])) in self.edges


def test_forward_search_escapes_reverse_fanin_expansion_limit_without_ignoring_costs():
    g=Graph();usage=defaultdict(set);usage[1].add('other');history=defaultdict(float)
    args=(g,2,{0},{0:0.},1.,usage,history,1000,set(),{},'a',0.,4.,10,lambda:None)
    path,expanded,_=connection_search(*args);assert path is None and expanded==11
    path,expanded,_=connection_search(*args,reverse=False)
    assert [(u,v) for u,v,p,d in path]==[(0,1),(1,2)] and expanded==2


def test_forward_tree_start_cost_preserves_existing_arrival_and_reservations():
    g=Graph();g.edges={(0,3):.1,(3,2):.1,(1,2):.01}
    args=(g,2,{0,1},{0:0.,1:10.},1.,defaultdict(set),defaultdict(float),1,set(),{},'a',0.,4.,100,lambda:None)
    path,_,_=connection_search(*args,reverse=False);assert path[0][0]==0
    blocked=list(args);blocked[8]={3};path,_,_=connection_search(*blocked,reverse=False);assert path[0][0]==1
    blocked[9]={2:'other'};assert connection_search(*blocked,reverse=False)[0] is None


def test_router_fallback_preserves_directed_pips_and_full_connectivity():
    g=Graph();g.edges[0,1]=100.;net=dict(name='a',source=0,sinks=[2],criticality=1.);events=[]
    result=route(g,[net],max_expansions=10,progress=events.append)
    assert result['status']=='routed' and validate(g,[net],result)['connectivity_verified']
    assert any(e.get('event')=='source_direction_fallback' and e['connected'] for e in events)
