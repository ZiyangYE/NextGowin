import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
from build_timing_model import build,read_snapshot
from extract_timing import extract
from validate_timing_model import validate
from extend_ff_model import extend


class RuntimeModelTest(unittest.TestCase):
    def test_incomplete_snapshot_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)/'snapshot.log'
            path.write_text('SNAPSHOT selector=1 width=4 groups=9\nROW 0 0\n')
            with self.assertRaises(ValueError):
                read_snapshot(path)

    def test_actual_runtime_rows_and_reports(self):
        raw = ROOT/'results/timing/vendor_split/timing.json'
        snapshot = ROOT/'results/gowin_snapshot_002/snapshot.log'
        report = ROOT/'results/gowin_probe_005/probe.tr'
        netlist = ROOT/'results/gowin_probe_005/probe.vo'
        if not all(p.exists() for p in [raw,snapshot,report,netlist]):
            self.skipTest('Vendor experiment artifacts unavailable')
        data = json.loads(raw.read_text())
        model = build(data,snapshot)
        self.assertEqual(model['validated_rows'],317)
        self.assertEqual(model['lut_envelope_ps'],521)
        self.assertTrue(validate(model,[extract(report,netlist)])['all_matched'])
        data['extended_55']['groups'][0]['rows'][0]['values'][0] += .001
        with self.assertRaises(ValueError):
            build(data,snapshot)

    def test_wrong_report_corner_rejected(self):
        with self.assertRaises(ValueError):
            validate({'part':'A','corners':{'setup':'slow','hold':'fast'},'runtime_rows':[]},
                     [{'metadata':{'Part Number':'A','Setup Delay Model':'other','Hold Delay Model':'fast'}}])

    def test_ff_signatures_and_signed_report_adjustments(self):
        model_path=ROOT/'results/timing/gowin138_model.json'
        trace_path=ROOT/'results/gowin_ff_trace_004/manifest.json'
        if not model_path.exists() or not trace_path.exists():
            self.skipTest('FF query artifacts unavailable')
        trace=json.loads(trace_path.read_text())
        model=extend(json.loads(model_path.read_text()),trace,trace_path.parent)
        ff=model['ff_data_constraints']
        self.assertEqual(ff['setup']['envelope_ps'],263)
        self.assertEqual(ff['hold']['envelope_ps'],23)
        source=extract(ROOT/'results/gowin_probe_005/probe.tr',ROOT/'results/gowin_probe_005/probe.vo')
        checks=validate(model,[source])
        self.assertTrue(checks['all_matched'])
        self.assertEqual(sum(c['kind']=='DFFRE D setup' for c in checks['checks']),8)
        self.assertEqual(sum(c['kind']=='DFFRE D hold' for c in checks['checks']),8)
        model['ff_data_constraints']['hold']['report_adjustment_sign']=-1
        self.assertFalse(validate(model,[source])['all_matched'])
        trace['queries'][1]['arg6']=0
        with self.assertRaises(ValueError):
            extend(json.loads(model_path.read_text()),trace,trace_path.parent)


if __name__=='__main__':
    unittest.main()
