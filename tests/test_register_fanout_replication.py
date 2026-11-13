import copy
from pathlib import Path
import sys
import tempfile
import unittest
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
from native_register_replication import replicate,validate_graph,prove_sdk,collapse_emitted


def fixture():
    cells={}
    for i in range(60):
        cells['ff'+str(i)]=dict(type='DFFRE',parameters={'INIT':'0'},attributes={},
            port_directions={'D':'input','CLK':'input','CE':'input','RESET':'input','Q':'output'},
            connections={'D':[4+i],'CLK':[2],'CE':[3],'RESET':['0'],'Q':[100+i]})
    for i in range(120):
        cells['sink'+str(i)]=dict(type='LUT1',parameters={'INIT':'10'},attributes={},
            port_directions={'I0':'input','F':'output'},connections={'I0':[100],'F':[1000+i]})
    return {'modules':{'top':dict(attributes={},ports={},netnames={},cells=cells)}}


class RegisterFanoutReplicationTests(unittest.TestCase):
    def test_constant_initialized_state_and_exact_graph_contract(self):
        source=fixture();before=copy.deepcopy(source);candidate,record=replicate(source,limit=24,area_allowance=.02)
        self.assertEqual(source,before);self.assertEqual(record['clones'],1)
        self.assertEqual(validate_graph(source,candidate,record)['clones'],1)
        self.assertEqual(collapse_emitted(candidate,record),source)
        broken=copy.deepcopy(candidate)
        broken['modules']['top']['cells'][record['copies'][0]['clone']]['connections']['CE']=['0']
        with self.assertRaisesRegex(ValueError,'function/control'):validate_graph(source,broken,record)
        with self.assertRaisesRegex(ValueError,'transition'):collapse_emitted(broken,record)
        broken=copy.deepcopy(candidate);broken['modules']['top']['cells']['ff2']['connections']['D']=['1']
        with self.assertRaisesRegex(ValueError,'beyond'):validate_graph(source,broken,record)

    def test_undefined_init_preserved_state_and_clock_load_are_rejected(self):
        for mode in ('init','preserve','clock','async_reg','net_async_reg'):
            with self.subTest(mode=mode):
                source=fixture();cells=source['modules']['top']['cells']
                if mode=='init':cells['ff0']['parameters']['INIT']='x'
                elif mode=='preserve':cells['ff0']['attributes']['keep']='1'
                elif mode=='async_reg':cells['ff0']['attributes']['ASYNC_REG']='TRUE'
                elif mode=='net_async_reg':source['modules']['top']['netnames']['synchronizer']=dict(bits=[100],attributes={'syn_async_reg':'1'})
                else:cells['ff1']['connections']['CLK']=[100]
                candidate,record=replicate(source,limit=24,area_allowance=.02)
                self.assertEqual(candidate,source);self.assertEqual(record['clones'],0)

    def test_sdk_initialization_and_transition_with_negative_control(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'results') as temp:
            self.assertEqual(prove_sdk(Path(temp)/'positive')['status'],'proved')
            negative=Path(temp)/'negative'
            with self.assertRaises(RuntimeError):prove_sdk(negative,negative_control=True)
            self.assertIn('proof did fail',(negative/'proof.console.log').read_text())


if __name__=='__main__':unittest.main()
