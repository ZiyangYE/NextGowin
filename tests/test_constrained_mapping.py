import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
from constrained_mapping import constraints,verify_annotations,run,analyze
from gowin_blif import read_blif
from timing_portfolio import select


class ConstrainedMappingTest(unittest.TestCase):
    def test_portfolio_protects_seed_when_remap_regresses_or_exceeds_area(self):
        candidates={name:dict(luts=area,timing=dict(wns_ps=wns,tns_ps=min(0,wns)))
            for name,area,wns in [('baseline',100,100),('acd',106,200),('acd_remap',95,50)]}
        self.assertEqual(select(candidates,.05),('baseline',105))
        self.assertEqual(select(candidates,None),('acd',None))
    def test_unknown_and_missing_constraints_rejected(self):
        network=dict(inputs=['a'],outputs=['y'])
        for data in [dict(units='ps',input_arrivals_ps={'b':0},default_output_required_ps=10),
                     dict(units='ns',default_output_required_ps=10),
                     dict(units='ps'),dict(units='ps',hold=0,default_output_required_ps=10)]:
            with self.assertRaises(ValueError):
                constraints(data,network)

    def test_silently_dropped_or_fractional_timing_rejected(self):
        with self.assertRaises(ValueError):
            verify_annotations('.model x\n.end',{'a':5000},{'y':6000},521)
        with self.assertRaises(ValueError):
            verify_annotations('.input_arrival a 9.59693 9.59693\n.output_required y 11.5163 11.5163',
                               {'a':5000},{'y':6000},521)

    def test_deep_datapath_forward_and_backward_timing(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'deep.blif'
            lines=['.model deep','.inputs a','.outputs n1500']
            for i in range(1,1501):
                lines += [f'.names {"a" if i==1 else "n"+str(i-1)} n{i}','1 1']
            path.write_text('\n'.join(lines+['.end'])+'\n')
            timing=analyze(read_blif(path),{'a':5000},{'n1500':1000000},521)
            self.assertEqual(timing['outputs']['n1500']['arrival_ps'],786500)
            self.assertEqual(timing['input_required_ps']['a'],218500)
            self.assertEqual(len(timing['outputs']['n1500']['critical_path']),1501)

    def test_native_mapping_uses_late_inputs_and_reports_impossible_deadline(self):
        binary=ROOT/'build/abc/_TEST/abc.exe'
        model=ROOT/'results/timing/gowin138_model.json'
        if not binary.exists() or not model.exists():
            self.skipTest('ABC/model unavailable')
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);src=root/'source.blif'
            lines=['.model late','.inputs a b c d e f','.outputs y','.names a b c d e f y']
            lines += [''.join(str((x>>i)&1) for i in range(6))+' 1' for x in range(64)
                if ((x&31).bit_count()%2)&((x>>5)&1)]
            src.write_text('\n'.join(lines+['.end'])+'\n')
            results={}
            for name,port,required in [('a','a',6000),('b','b',6000),('bad','f',5500)]:
                constraint=root/(name+'.json')
                constraint.write_text(json.dumps(dict(units='ps',default_output_required_ps=required,input_arrivals_ps={port:5000})))
                results[name]=run(src,constraint,model,root/name)
                self.assertTrue(results[name]['cec']['equivalent'])
                self.assertTrue(results[name]['annotations_preserved'])
            a=results['a']['independent_timing']['outputs']['y']['path_lut_levels']
            b=results['b']['independent_timing']['outputs']['y']['path_lut_levels']
            self.assertEqual(a['a'],1);self.assertEqual(a['b'],2)
            self.assertEqual(b['b'],1);self.assertEqual(b['a'],2)
            self.assertFalse(results['bad']['independent_timing']['meets_required'])
            self.assertEqual(results['bad']['independent_timing']['wns_ps'],-21)
            self.assertTrue(results['bad']['native_relaxed_deadlines'])


if __name__=='__main__':
    unittest.main()
