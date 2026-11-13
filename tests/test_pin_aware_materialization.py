import copy
from pathlib import Path
import sys
import tempfile
import unittest
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'experiments'))
from pin_aware_lut_materialization import optimize
from native_boundary_cec import prove


def fixture():
    return dict(modules={'top':dict(attributes={},netnames={},
        ports={**{n:dict(direction='input',bits=[b]) for n,b in [('a',2),('b',3),('s',4)]},'y':dict(direction='output',bits=[6])},
        cells={'ng_acd_root':dict(type='LUT3',parameters={'INIT':'11001010'},attributes={'ng_generated':'1'},
            port_directions={'I0':'input','I1':'input','I2':'input','F':'output'},connections={'I0':[2],'I1':[3],'I2':[4],'F':[6]})})})


class PinAwareMaterializationTests(unittest.TestCase):
    def test_truth_rebinding_and_fast_pin_for_late_data(self):
        source=fixture();before=copy.deepcopy(source);candidate,r=optimize(source,input_arrivals={2:5000,3:0,4:100})
        self.assertEqual(source,before);self.assertEqual(r['changed'],1)
        c=candidate['modules']['top']['cells']['ng_acd_root']
        self.assertEqual(c['type'],'LUT4');self.assertEqual(c['connections']['I3'],[2])
        with tempfile.TemporaryDirectory(dir=ROOT/'results') as temp:
            self.assertTrue(prove(source,candidate,Path(temp)/'good')[0]['cec']['equivalent'])
            c['parameters']['INIT']='0'*16
            with self.assertRaises(ValueError):prove(source,candidate,Path(temp)/'bad')

    def test_clock_cone_and_preserved_cell_are_unchanged(self):
        for mode in ('clock','preserved'):
            source=fixture();m=source['modules']['top']
            if mode=='preserved':m['cells']['ng_acd_root']['attributes']['dont_touch']='1'
            else:
                m['cells']['clock_buffer']=dict(type='INV',parameters={},attributes={},port_directions={'I':'input','O':'output'},connections={'I':[6],'O':[8]})
                m['cells']['state']=dict(type='DFFRE',parameters={'INIT':'0'},attributes={},port_directions={'CLK':'input','D':'input','Q':'output','CE':'input','RESET':'input'},
                    connections={'CLK':[8],'D':[3],'Q':[9],'CE':['1'],'RESET':['0']})
            candidate,r=optimize(source,input_arrivals={2:5000})
            self.assertEqual(candidate,source);self.assertEqual(r['changed'],0)


if __name__=='__main__':unittest.main()
