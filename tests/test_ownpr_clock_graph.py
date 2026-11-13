import json,sys,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from ownpr_clock_graph import ClockGraph


class NativeClockGraphTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.graph=ClockGraph(ROOT/'results/native_clock_inventory_005')
        reference=json.loads((ROOT/'results/native_global_clock_001/native_endpoint_routes.json').read_text())
        cls.paths=[s['path_sink_to_source'] for n in reference['nets'] for s in n['sinks'] if s.get('native_pin_type')==670]
        cls.own=json.loads((ROOT/'results/ownpr_clock_tree_001/clock_tree.json').read_text())

    def test_actual_vendor_clock_paths_exist_in_independently_loaded_graph(self):
        result=self.graph.audit_paths(self.paths)
        self.assertEqual(result['status'],'clock_paths_match');self.assertEqual(result['paths'],18)
        self.assertEqual(result['missing'],[]);self.assertFalse(result['configuration_verified'])

    def test_local_clock_constraint_cannot_be_silently_ignored(self):
        source=self.own['source'];sinks=[p[0] for p in self.own['paths_sink_to_source']]
        with self.assertRaisesRegex(ValueError,'permitted clock routing policy'):self.graph.candidate(source,sinks,'LOCAL_CLOCK')
        candidate=self.graph.candidate(source,sinks,'AUTO')
        self.assertEqual(len(candidate['paths_sink_to_source']),18)
        self.assertFalse(candidate['configuration_verified']);self.assertFalse(candidate['resource_capacity_verified'])

    def test_missing_clock_edge_is_detected(self):
        destination,source=self.paths[0][0:2]
        a=self.graph.by_coordinate[tuple(source)];b=self.graph.by_coordinate[tuple(destination)];old=self.graph.adj[a]
        try:
            self.graph.adj[a]=[x for x in old if x!=b]
            result=self.graph.audit_paths(self.paths)
            self.assertEqual(result['status'],'incomplete_or_mismatched');self.assertTrue(result['missing'])
        finally:self.graph.adj[a]=old


if __name__=='__main__':unittest.main()
