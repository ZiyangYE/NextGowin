import json,sys,unittest
from collections import defaultdict
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from ownpr_device import Device,RoutingGraph
from ownpr_expand_routes import expand


class PhysicalRouteExpansionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.graph=RoutingGraph(Device(),ROOT/'results/native_rrg_001')
        folder=ROOT/'results/ownpr_int2float_route_005'
        cls.nets=json.loads((folder/'nets.json').read_text());cls.routes=json.loads((folder/'routes.json').read_text())
        cls.expanded=json.loads((ROOT/'results/ownpr_int2float_wire_timing_001/expanded_routes.json').read_text())
        cls.edges=defaultdict(set)
        for net in cls.expanded['nets'].values():
            for row in net['parents']:
                if row['parent'] is not None:cls.edges[tuple(row['parent'])].add(tuple(row['node']))

    def test_reconstruct_observed_physical_trees(self):
        result=expand(self.graph,self.nets,self.routes,lambda n:sorted(self.edges.get(n,())))
        self.assertEqual(result['physical_nodes'],1546)
        self.assertEqual(result['intrinsic_edges'],524)
        self.assertEqual(result['local_edges'],896)
        self.assertEqual(sum(len(n['sources']) for n in result['nets'].values()),126)
        self.assertFalse(result['timing_signoff'])

    def test_alias_occupancy_does_not_imply_directed_reachability(self):
        edges={n:set(v) for n,v in self.edges.items()}
        before,node=next((n,v) for n,values in edges.items() for v in values if n[:2]!=v[:2])
        edges[before].remove(node);edges.setdefault(node,set()).add(before)
        with self.assertRaisesRegex(ValueError,'not a directed physical connection'):
            expand(self.graph,self.nets,self.routes,lambda n:sorted(edges.get(n,())))


if __name__=='__main__':unittest.main()
