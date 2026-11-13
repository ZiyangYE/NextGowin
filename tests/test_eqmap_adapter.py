from pathlib import Path
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'));sys.path.insert(0,str(ROOT/'experiments'))
from eqmap_cut_adapter import BINARY,optimize
from gw5a_functional_map import blif


@unittest.skipUnless(BINARY.exists(),'Optional upstream EqMap executable is not built')
class UpstreamEqMapTests(unittest.TestCase):
    def test_six_input_author_result_has_independent_native_truth_proof(self):
        inputs=['eq_const_0','b','c','d','e','late']
        # A six-input function with a late-select decomposition. The adapter
        # must preserve names and prove its legal LUT4/private-MUX realization.
        truth=sum((((v&1)^((v>>1)&1)^((v>>2)&1)) if v>>5 else (((v>>3)&1)&((v>>4)&1)))<<v for v in range(64))
        network=dict(inputs=inputs,outputs=['answer'],nodes=[dict(inputs=inputs,output='answer',truth=truth)])
        with tempfile.TemporaryDirectory(dir=ROOT/'results') as temp:
            p=Path(temp);source=p/'source.blif';source.write_text(blif(network))
            report,actual=optimize(source,p/'mapped',min_depth=True)
            self.assertTrue(report['proof']['equivalent'])
            self.assertTrue(all(len(n['inputs'])<=4 for n in actual['nodes']))
            self.assertEqual(actual['inputs'],inputs)
            self.assertEqual(actual['outputs'],['answer'])


if __name__=='__main__':unittest.main()
