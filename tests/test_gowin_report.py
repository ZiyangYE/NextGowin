from pathlib import Path
import sys
import unittest
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
from gowin_report import summaries


class ReportTest(unittest.TestCase):
    def test_missing_fields_are_not_timing_success(self):
        with self.assertRaises(ValueError):
            summaries('Tool exited successfully')

    def test_hold_violation_and_requested_uncertainty(self):
        text='<Numbers of Setup Violated Endpoints>:0\n<Numbers of Hold Violated Endpoints>:1\n'
        self.assertFalse(summaries(text)['timing_closed'])
        with self.assertRaises(ValueError):
            summaries(text,.05)
        text+='3.3.2 Hold Analysis Report\n 1.234 0.050 tUnc\n'
        self.assertTrue(summaries(text,.05)['report_confirms_hold_uncertainty'])


if __name__=='__main__':
    unittest.main()
