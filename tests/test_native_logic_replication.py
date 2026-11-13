from pathlib import Path
import sys
import tempfile
import unittest
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'));sys.path.insert(0,str(ROOT/'experiments'))
from native_logic_replication import replicate,prove


def fixture():
    def lut(ins,out):
        return dict(type='LUT2',parameters={'INIT':'1000'},attributes={},connections=dict(I0=[ins[0]],I1=[ins[1]],F=[out]),port_directions=dict(I0='input',I1='input',F='output'))
    cells={'source':lut([3,4],10)}
    cells.update({'sink'+str(i):lut([10,5],20+i) for i in range(260)})
    return dict(modules={'top':dict(attributes={},cells=cells,netnames={},ports={'a':dict(direction='input',bits=[3,4,5]),'q':dict(direction='output',bits=list(range(20,280)))})})


class NativeLogicReplicationTests(unittest.TestCase):
    def test_area_bounded_real_lut_functions_pass_independent_cec(self):
        source=fixture();after,record=replicate(source)
        self.assertGreater(record['clones'],0)
        self.assertLessEqual(record['modules'][0]['final_luts'],record['modules'][0]['cap'])
        with tempfile.TemporaryDirectory(dir=ROOT/'results') as temp:
            proof=prove(source,after,Path(temp)/'proof')
            self.assertTrue(proof[0]['cec']['equivalent'])
            clone=next(c for n,c in after['modules']['top']['cells'].items() if '_ng_logic_replica_' in n)
            clone['parameters']['INIT']='1001'
            with self.assertRaisesRegex(ValueError,'CEC failed'):prove(source,after,Path(temp)/'tampered')

    def test_kept_or_async_control_drivers_are_not_replicated(self):
        source=fixture();source['modules']['top']['cells']['source']['attributes']['keep']='1'
        after,record=replicate(source);self.assertEqual(after,source);self.assertEqual(record['clones'],0)
        source=fixture();source['modules']['top']['cells']['async']=dict(type='DFFCE',parameters={'INIT':'0'},attributes={},
            connections=dict(CLEAR=[10],D=[3],CLK=[4],CE=['1'],Q=[300]),
            port_directions=dict(CLEAR='input',D='input',CLK='input',CE='input',Q='output'))
        after,record=replicate(source);self.assertEqual(after,source);self.assertEqual(record['clones'],0)


if __name__=='__main__':unittest.main()
