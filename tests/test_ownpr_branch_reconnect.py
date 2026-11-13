import sys,time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from ownpr_branch_reconnect import reconnect


class Graph:
    def __init__(self):
        self.edges=[(0,1,1.),(1,2,1.),(1,3,1.),(0,4,.2),(4,2,.2),(0,5,.3),(5,2,.3)]
    def node(self,r,c,n):return n
    def begin_net(self,n):pass
    def uphill(self,n):return [(a,w,(0,0,a,b)) for a,b,w in self.edges if b==n]


def fixture():
    return dict(name='n',source=0,sinks=[2,3]),dict(nodes=[0,1,2,3],pips=[(0,0,0,1),(0,0,1,2),(0,0,1,3)])


def test_reconnection_keeps_other_branch_and_explores_distinct_paths():
    net,record=fixture();rows,stats=reconnect(Graph(),net,record,2,{n:'n' for n in record['nodes']},time.monotonic()+5)
    assert any((0,0,0,4) in r['pips'] for r in rows)
    assert any((0,0,0,5) in r['pips'] for r in rows)
    assert all((0,0,0,1) in r['pips'] and (0,0,1,3) in r['pips'] for r in rows)
    assert stats['removed_pips']==1


def test_other_net_ownership_is_hard_constraint():
    net,record=fixture();owners={n:'n' for n in record['nodes']};owners.update({4:'other',5:'other'})
    rows,_=reconnect(Graph(),net,record,2,owners,time.monotonic()+5)
    assert rows and all(4 not in r['nodes'] and 5 not in r['nodes'] for r in rows)


def test_internal_sink_is_not_ripped_up():
    net,record=fixture();rows,stats=reconnect(Graph(),net,record,1,{},time.monotonic()+5)
    assert not rows and stats['reason']=='not_terminal_sink'
