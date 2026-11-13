import sys
from pathlib import Path
import unittest
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from routing_path_diagnosis import diagnose
from path_accounting import account

class RoutingDiagnosisTests(unittest.TestCase):
    def test_clock_is_not_counted_as_data_routing(self):
        p=ROOT/'results/fanout_development_suite_001/ctrl/own_pnr/mapped.tr'
        if not p.exists():self.skipTest('Native report unavailable')
        text=p.read_text();r=diagnose(text)[0];old=account(text)[0]
        self.assertAlmostEqual(r['data_wire_ns'],2.070,places=3)
        self.assertAlmostEqual(r['launch_clock_delay_ns'],4.075,places=3)
        self.assertAlmostEqual(r['data_wire_ns']+r['data_cell_ns'],old['data_delay_sum_ns'])
        self.assertEqual(r['wire_edges'],1);self.assertIsNone(r['edges'][0]['manhattan_tiles'])

    def test_dedicated_zero_delay_links_are_separate(self):
        p=ROOT/'results/fanout_development_suite_001/sin/own_pnr/mapped.tr'
        if not p.exists():self.skipTest('Native report unavailable')
        r=diagnose(p.read_text())[0]
        self.assertAlmostEqual(r['data_wire_ns'],29.560,places=3)
        self.assertEqual(r['zero_delay_edges'],12)
        self.assertEqual(r['nonzero_wire_edges'],38)
        self.assertEqual(r['edges'][0]['manhattan_tiles'],40)
        self.assertEqual(r['edges'][0]['source_fanout'],11)

if __name__=='__main__':unittest.main()
