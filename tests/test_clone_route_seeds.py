import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from ownpr_clone_route_seeds import reuse_by_source


class Graph:
    def node(self,r,c,n):return n
    def check_pip(self,p):return p[0:2]==[0,0] or tuple(p[0:2])==(0,0)


def fixture():
    graph=Graph();old=[dict(name='old',source=1,sinks=[3,4])];route=dict(status='routed',nets={'old':dict(nodes=[1,2,3,4],pips=[[0,0,1,2],[0,0,2,3],[0,0,2,4]])})
    new=[dict(name='new_clone_net',source=1,sinks=[4]),dict(name='moved_original',source=5,sinks=[3])]
    return graph,old,route,new


def test_renamed_net_reuses_only_common_sink_tree_at_same_physical_source():
    g,old,routes,new=fixture();seeds,audit=reuse_by_source(g,old,routes,new,new,set(),{})
    assert seeds['new_clone_net']==dict(nodes=[1,2,4],pips=[[0,0,1,2],[0,0,2,4]],retained_sinks=[4])
    assert 'moved_original' not in seeds and audit['transfers'][0]['donor']=='old'
    assert routes['nets']['old']['nodes']==[1,2,3,4]


def test_foreign_occupancy_and_new_endpoint_overlap_prevent_transfer():
    g,old,routes,new=fixture()
    assert reuse_by_source(g,old,routes,new,new,{2},{})[0]=={}
    conflict=new+[dict(name='foreign',source=2,sinks=[9])]
    assert reuse_by_source(g,old,routes,new,conflict,set(),{})[0]=={}
