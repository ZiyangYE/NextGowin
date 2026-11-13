from pathlib import Path
import sys
import unittest
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from hierarchy_islands import detect


class DSPIslandPolicyTests(unittest.TestCase):
    def test_decision_depends_on_structure_and_verified_operators(self):
        state='\n'.join(f'VERIFIC_DFFRS state{i}(.d(d),.clk(clk),.s(0),.r(0),.q(q));' for i in range(20))
        body='verified_product mul0(.a(a),.b(b),.o(c));\nverified_product mul1(.a(a),.b(b),.o(c));\n'+state
        a='module alpha(input clk,d,a,b,output q,c);\n'+body+'\nendmodule'
        b=a.replace('module alpha','module arbitrary_name')
        self.assertEqual(detect(a,[]),[])
        one=detect(a,['verified_product']);two=detect(b,['verified_product'])
        self.assertEqual(one[0]['multiply_instances'],two[0]['multiply_instances'])
        self.assertEqual(one[0]['direct_state_instances'],two[0]['direct_state_instances'])
        self.assertEqual(two[0]['module'],'arbitrary_name')
        self.assertEqual(detect(a.replace(state,'// '+state.replace('\n','\n// ')),['verified_product']),[])
        without_products=a.replace('verified_product','unverified_product')
        self.assertEqual(detect(without_products,[],min_multipliers=0)[0]['direct_state_instances'],20)


if __name__=='__main__':unittest.main()
