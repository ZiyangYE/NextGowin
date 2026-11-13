from pathlib import Path
import sys
import tempfile
import unittest
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
from gowin_blif import cube_truth,read_blif,emit,readback
from run_abc_reproduction import cec,stats


class BlifTest(unittest.TestCase):
    def test_truth_polarity_and_input_order(self):
        self.assertEqual(cube_truth(['a','b'],[['10','1']]),2)
        self.assertEqual(cube_truth(['a','b'],[['10','0']]),13)
        self.assertEqual(cube_truth([],[]),0)
        self.assertEqual(cube_truth([],[['1']]),1)

    def test_unsupported_latch_and_cycle_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'bad.blif'
            for text in ['.model x\n.inputs a\n.outputs q\n.latch a q\n.end\n',
                         '.model x\n.inputs a\n.outputs q\n.names q t\n1 1\n.names t q\n1 1\n.end\n']:
                path.write_text(text)
                with self.assertRaises(ValueError):
                    read_blif(path)

    def test_emitted_init_readback_formally_equivalent(self):
        binary=ROOT/'build/abc/_TEST/abc.exe'
        if not binary.exists():
            self.skipTest('ABC not built')
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);src=root/'input.blif';back=root/'back.blif'
            src.write_text('.model t\n.inputs a b c\n.outputs y z a\n.names a b x\n10 0\n.names x c y\n1- 1\n-1 1\n.names z\n1\n.end\n')
            text=emit(read_blif(src))
            back.write_text(readback(text))
            self.assertTrue(cec(binary,src,back,root/'cec.log',30)['equivalent'])
            # Corrupt a real INIT bit: the readback must no longer prove equal.
            altered=text.replace("lut0.INIT=4'hD", "lut0.INIT=4'hF")
            self.assertNotEqual(text,altered)
            back.write_text(readback(altered))
            self.assertFalse(cec(binary,src,back,root/'wrong.log',30)['equivalent'])

    def test_stats_require_actual_mapping_output(self):
        with self.assertRaises(ValueError):
            stats('======== ABC command line print_stats; echo NEXTGOWIN_DONE')


if __name__=='__main__':
    unittest.main()
