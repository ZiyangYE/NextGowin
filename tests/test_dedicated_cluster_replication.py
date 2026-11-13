import copy
from pathlib import Path
import sys
import tempfile
import unittest
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from native_cluster_replication import replicate
from native_boundary_cec import prove
from gw5a_functional_map import validate_packing


def fixture():
    cells={}
    for i in range(100):
        cells['ng_acd_leaf_'+str(i)]=dict(type='LUT2',parameters={'INIT':'0110' if i==0 else '1110'},attributes={'ng_generated':'1',**({'keep':'1'} if i<4 else {})},
            port_directions={'I0':'input','I1':'input','F':'output'},connections={'I0':[3],'I1':[4+i%2],'F':[1000+i]})
    cells['ng_acd_root']=dict(type='MUX2_LUT5',parameters={},attributes={},
        port_directions={'I0':'input','I1':'input','S0':'input','O':'output'},
        connections={'I0':[1000],'I1':[1001],'S0':[7],'O':[2000]})
    for i in range(70):
        cells['ff'+str(i)]=dict(type='DFFRE',parameters={'INIT':'0'},attributes={},
            port_directions={'D':'input','CLK':'input','CE':'input','RESET':'input','Q':'output'},
            connections={'D':[2000],'CLK':[2],'CE':['1'],'RESET':['0'],'Q':[20000+i]})
    ports={n:dict(direction='input',bits=[b]) for n,b in [('clk',2),('a',3),('b',4),('c',5),('s',7)]}
    return {'modules':{'top':dict(attributes={},cells=cells,ports=ports,netnames={})}}


class DedicatedClusterReplicationTests(unittest.TestCase):
    def test_whole_cluster_budget_exclusivity_and_actual_truth_cec(self):
        source=fixture();before=copy.deepcopy(source);candidate,record=replicate(source,limit=16,area_allowance=.02)
        self.assertEqual(source,before);self.assertEqual(record['clusters'],1)
        self.assertEqual(record['added_lut_slots'],2)
        self.assertEqual(validate_packing(candidate)['mux5_checked'],2)
        with tempfile.TemporaryDirectory(dir=ROOT/'results') as temp:
            self.assertTrue(prove(source,candidate,Path(temp)/'good')[0]['cec']['equivalent'])
            broken=copy.deepcopy(candidate)
            clone=record['modules'][0]['copies'][0]['members']['ng_acd_leaf_0']
            broken['modules']['top']['cells'][clone]['parameters']['INIT']='1001'
            with self.assertRaises(ValueError):prove(source,broken,Path(temp)/'bad')

    def test_preserved_cluster_and_clock_sink_are_not_replicated(self):
        for mode in ('preserved','user_keep','clock'):
            with self.subTest(mode=mode):
                source=fixture()
                if mode=='preserved':source['modules']['top']['cells']['ng_acd_leaf_0']['attributes']['dont_touch']='1'
                elif mode=='user_keep':source['modules']['top']['cells']['ng_acd_leaf_0']['attributes'].pop('ng_generated')
                else:source['modules']['top']['cells']['ff0']['connections']['CLK']=[2000]
                candidate,record=replicate(source,limit=16,area_allowance=.02)
                self.assertEqual(candidate,source);self.assertEqual(record['clusters'],0)

    def test_nested_mux6_clones_both_private_mux5_children(self):
        source=fixture();m=source['modules']['top'];cells=m['cells']
        for i in range(100,200):
            c=copy.deepcopy(cells['ng_acd_leaf_0']);c['connections']['F']=[1000+i];cells['ng_acd_leaf_'+str(i)]=c
        branch=copy.deepcopy(cells['ng_acd_root']);branch['connections'].update(I0=[1002],I1=[1003],O=[2001])
        cells['ng_acd_branch']=branch
        root=copy.deepcopy(branch);root['type']='MUX2_LUT6';root['connections'].update(I0=[2000],I1=[2001],S0=[3],O=[3000])
        cells['ng_acd_root6']=root
        for c in cells.values():
            if c['type']=='DFFRE':c['connections']['D']=[3000]
        candidate,record=replicate(source,limit=16,area_allowance=.02)
        self.assertEqual(record['clusters'],1);self.assertEqual(record['added_lut_slots'],4)
        self.assertEqual(validate_packing(candidate),dict(mux5_checked=4,mux6_checked=2))
        with tempfile.TemporaryDirectory(dir=ROOT/'results') as temp:
            self.assertTrue(prove(source,candidate,Path(temp)/'cec')[0]['cec']['equivalent'])


if __name__=='__main__':unittest.main()
