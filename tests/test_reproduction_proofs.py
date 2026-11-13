from pathlib import Path
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
from run_abc_reproduction import network_sha
from collect_acd_corpus import records


class ProofReuseTest(unittest.TestCase):
    def test_only_comment_changes_reuse_network_digest(self):
        with tempfile.TemporaryDirectory() as temp:
            a=Path(temp)/'a.blif';b=Path(temp)/'b.blif'
            logic='.model t\n.inputs a\n.outputs y\n.names a y\n1 1\n.end\n'
            a.write_text('# timestamp one\n'+logic)
            b.write_text('# timestamp two\n\n'+logic)
            self.assertEqual(network_sha(a),network_sha(b))
            b.write_text(logic.replace('1 1','0 1'))
            self.assertNotEqual(network_sha(a),network_sha(b))

    def test_truncated_corpus_is_not_silent_success(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'cuts.bin';path.write_bytes(b'ACDCUT01\x01')
            with self.assertRaises(ValueError):
                list(records(path))


if __name__=='__main__':
    unittest.main()
