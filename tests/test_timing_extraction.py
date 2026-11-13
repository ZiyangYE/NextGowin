import importlib.util
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('timing', Path(__file__).resolve().parents[1]/'scripts/extract_timing.py')
timing = importlib.util.module_from_spec(spec)
spec.loader.exec_module(timing)


class TimingExtractionTest(unittest.TestCase):
    def test_separates_route_and_signed_constraint(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root/'n.vo').write_text('LUT4 \\a/b  (.I0(x), .F(y));\nDFFRE ff (.D(y));\n')
            (root/'r.tr').write_text('''<Part Number>: test
<Setup Delay Model>: slow
<Hold Delay Model>: fast
3.3.1 Setup Analysis Report
Path1
Data Arrival Path:
 1.000 0.700 tNET RR 1 R1C1 a/b/I0
 1.500 0.500 tINS RR 2 R1C1 a/b/F
 1.800 0.300 tNET RR 1 R1C2 ff/D
Data Required Path:
 9.700 -0.300 tSu 1 R1C2 ff
Path Statistics:
3.3.2 Hold Analysis Report
Path2
Data Required Path:
 0.012 0.012 tHld 1 R1C2 ff
Path Statistics:
''')
            result = timing.extract(root/'r.tr', root/'n.vo')
            self.assertEqual(len(result['arcs']), 3)
            self.assertEqual(result['arcs'][0]['from_pin'], 'I0')
            self.assertEqual(result['arcs'][0]['delay_ns'], .5)
            self.assertEqual(result['arcs'][1]['delay_ns'], -.3)
            self.assertEqual(result['arcs'][2]['kind'],'tHld')
            self.assertEqual(result['arcs'][2]['delay_ns'],.012)
            self.assertNotIn('tNET', [a['kind'] for a in result['arcs']])

    def test_rejects_missing_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root/'n.vo').write_text('')
            (root/'r.tr').write_text('not a report')
            with self.assertRaises(ValueError):
                timing.extract(root/'r.tr', root/'n.vo')


if __name__ == '__main__':
    unittest.main()
