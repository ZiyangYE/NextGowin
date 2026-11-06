import copy,sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from ownpr_router import route,validate

class Graph:
    def __init__(self):
        self.coordinates=[(0,0)]*6
        self.edges={(0,2),(2,3),(0,4),(4,3),(1,2),(2,5)}
    def downhill(self,n):return [(b,1.,(0,0,str(a),str(b))) for a,b in sorted(self.edges) if a==n]
    def node(self,r,c,w):return int(w)
    def check_pip(self,p):return (int(p[2]),int(p[3])) in self.edges

class RouterTests(unittest.TestCase):
    def test_reserved_dedicated_resource_is_never_used_by_ordinary_search(self):
        graph=Graph();nets=[dict(name='a',source=0,sinks=[3])]
        result=route(graph,nets,blocked_nodes={2})
        self.assertEqual(result['status'],'routed');self.assertNotIn(2,result['nets']['a']['nodes'])
        self.assertTrue(validate(graph,nets,result)['connectivity_verified'])
        with self.assertRaisesRegex(ValueError,'reserved'):route(graph,nets,blocked_nodes={3})
        class ReverseGraph(Graph):
            def uphill(self,n):return [(a,1.,(0,0,str(a),str(b))) for a,b in sorted(self.edges) if b==n]
        result=route(ReverseGraph(),nets,blocked_nodes={2})
        self.assertEqual(result['status'],'routed');self.assertNotIn(2,result['nets']['a']['nodes'])

    def test_reverse_search_keeps_forward_pip_orientation(self):
        class ReverseGraph(Graph):
            def uphill(self,n):return [(a,1.,(0,0,str(a),str(b))) for a,b in sorted(self.edges) if b==n]
        g=ReverseGraph();nets=[dict(name='a',source=0,sinks=[3],criticality=1),dict(name='b',source=1,sinks=[5],criticality=.5)]
        r=route(g,nets,max_iterations=10)
        self.assertEqual(r['status'],'routed');self.assertTrue(validate(g,nets,r)['connectivity_verified'])

    def test_negotiation_repairs_conflict_and_independent_validator(self):
        g=Graph();nets=[dict(name='a',source=0,sinks=[3],criticality=1),dict(name='b',source=1,sinks=[5],criticality=.5)]
        r=route(g,nets,max_iterations=5)
        self.assertEqual(r['status'],'routed');self.assertGreater(len(r['iterations']),1)
        self.assertTrue(validate(g,nets,r)['no_node_conflicts'])
        broken=copy.deepcopy(r);broken['nets']['a']['pips'].pop()
        with self.assertRaisesRegex(ValueError,'Disconnected'):validate(g,nets,broken)
        broken=copy.deepcopy(r);broken['nets']['a']['pips']=[(0,0,'0','2'),(0,0,'2','3')]
        with self.assertRaisesRegex(ValueError,'two nets'):validate(g,nets,broken)
        broken=copy.deepcopy(r);broken['nets']['a']['pips'].append((0,0,'0','5'))
        with self.assertRaisesRegex(ValueError,'Nonexistent'):validate(g,nets,broken)

    def test_unreachable_sink_is_not_success(self):
        g=Graph();r=route(g,[dict(name='x',source=3,sinks=[0])],max_iterations=2)
        self.assertEqual(r['status'],'unrouted');self.assertFalse(r['timing_signoff'])

    def test_shared_sink_is_not_a_legal_route(self):
        r=route(Graph(),[dict(name='a',source=0,sinks=[3]),dict(name='b',source=1,sinks=[3])],max_iterations=3)
        self.assertEqual(r['status'],'unrouted')

    def test_tree_attachment_accounts_for_existing_arrival(self):
        class TimingGraph(Graph):
            def __init__(self):
                self.coordinates=[(0,0)]*4;self.costs={(0,1):10.,(1,2):.1,(0,3):1.,(3,2):1.}
                self.edges=set(self.costs);self.heuristic_scale=0.
            def uphill(self,n):return [(a,self.costs[a,b],(0,0,str(a),str(b))) for a,b in sorted(self.edges) if b==n]
        nets=[dict(name='branch',source=0,sinks=[1,2],criticality=1.)]
        graph=TimingGraph();nearest=route(graph,nets)
        self.assertAlmostEqual(nearest['nets']['branch']['estimated_arrival_cost'][2],10.1)
        graph.root_arrival_weight=1.;timed=route(graph,nets)
        self.assertEqual(timed['status'],'routed');self.assertTrue(validate(graph,nets,timed)['connectivity_verified'])
        self.assertAlmostEqual(timed['nets']['branch']['estimated_arrival_cost'][2],2.)

    def test_region_exhaustion_widens_instead_of_claiming_disconnection(self):
        class RegionGraph(Graph):
            def __init__(self):super().__init__();self.widened=False
            def begin_net(self,net):self.search_region_active=not self.widened
            def widen_search(self,name):self.widened=True
            def uphill(self,n):
                return [(a,1.,(0,0,str(a),str(b))) for a,b in sorted(self.edges) if b==n] if self.widened else []
        graph=RegionGraph();nets=[dict(name='window',source=0,sinks=[3],criticality=1.)]
        result=route(graph,nets,max_iterations=3)
        self.assertEqual(result['status'],'routed');self.assertEqual(result['iterations'][0]['unrouted_sinks'],1)
        self.assertTrue(validate(graph,nets,result)['connectivity_verified'])

if __name__=='__main__':unittest.main()
