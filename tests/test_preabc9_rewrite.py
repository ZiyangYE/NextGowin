from pathlib import Path
import sys
import tempfile
import unittest
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'));sys.path.insert(0,str(ROOT/'experiments'))
from abc9_pre_rewrite import rewrite
from native_boundary_cec import prove


class PreABC9RewriteTests(unittest.TestCase):
    def test_unused_named_state_bits_are_reserved_during_reconstruction(self):
        cell=dict(type='$_MUX_',parameters={},attributes={},connections=dict(A=[2],B=[3],S=[4],Y=[5]),
                  port_directions=dict(A='input',B='input',S='input',Y='output'))
        source=dict(modules={'top':dict(attributes={},cells={'mux':cell},
            ports={'a':dict(direction='input',bits=[2,3,4]),'y':dict(direction='output',bits=[5])},
            netnames={'unused_state':dict(bits=[99999],attributes={'init':'1'})})})
        with tempfile.TemporaryDirectory(dir=ROOT/'results') as temp:
            p=Path(temp);after,record=rewrite(source,p/'rewrite')
            self.assertTrue(record[0]['proof']['equivalent'])
            bits={b for c in after['modules']['top']['cells'].values() for bs in c['connections'].values() for b in bs if isinstance(b,int)}
            self.assertNotIn(99999,bits);self.assertGreater(max(bits),99999)
            self.assertEqual(after['modules']['top']['netnames'],source['modules']['top']['netnames'])
            actual=prove(source,after,p/'readback');self.assertTrue(actual[0]['cec']['equivalent'])


if __name__=='__main__':unittest.main()
