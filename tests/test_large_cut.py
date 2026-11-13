"""Semantic checks for eight-variable cuts and conservative dead-cone recovery."""
import copy
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from ownpr_large_cut import exact_truth, native_leaf_slots, recover_dead_logic


def lut(inputs, output, truth):
    return dict(type='LUT'+str(len(inputs)), parameters={'INIT':format(truth, '0'+str(1<<len(inputs))+'b')},
                attributes={}, connections={**{'I'+str(i):[b] for i,b in enumerate(inputs)}, 'F':[output]},
                port_directions={**{'I'+str(i):'input' for i in range(len(inputs))}, 'F':'output'})


def fixture():
    parity4=sum((a.bit_count()%2)<<a for a in range(16))
    cells={'a':lut([1,2,3,4],9,parity4), 'b':lut([5,6,7,8],10,parity4), 'root':lut([9,10],11,6)}
    return {'modules':{'top':{'cells':cells,'ports':{'out':{'direction':'output','bits':[11]}},'netnames':{}}}}


class LargeCutTests(unittest.TestCase):
    def test_all_256_assignments_and_boundary_permutation(self):
        d=fixture()
        expected=sum((a.bit_count()%2)<<a for a in range(256))
        self.assertEqual(exact_truth(d,'root',range(1,9)),expected)
        self.assertEqual(exact_truth(d,'root',range(8,0,-1)),expected)

    def test_uncovered_and_duplicate_variables_reject(self):
        for boundary in ([1,2,3,4,5,6,7,12], [1,2,3,4,5,6,7,7]):
            with self.assertRaises(ValueError):exact_truth(fixture(),'root',boundary)

    def test_combinational_cycle_rejects(self):
        d=fixture();d['modules']['top']['cells']['a']['connections']['I0']=[11]
        with self.assertRaises(ValueError):exact_truth(d,'root',range(1,9))

    def test_lut_mutation_is_detected(self):
        d=fixture();before=exact_truth(d,'root',range(1,9))
        d['modules']['top']['cells']['root']['parameters']['INIT']='0111'
        self.assertNotEqual(exact_truth(d,'root',range(1,9)),before)

    def test_native_wide_child_order(self):
        self.assertEqual(native_leaf_slots(6,1),[2,3,0,1])
        self.assertEqual(native_leaf_slots(7,3),[6,7,4,5,2,3,0,1])

    def test_dce_keeps_state_inputs_anchors_and_observed_nets(self):
        d=fixture();m=d['modules']['top'];c=m['cells']
        for i,n in enumerate(('dead','kept','anchor','state_d')):c[n]=lut([1],20+i,2)
        c['ff']={'type':'DFF','connections':{'D':[23],'Q':[30]},'port_directions':{'D':'input','Q':'output'}}
        m['netnames']['observe']={'bits':[21],'attributes':{'keep':'1'}}
        layout={'clusters':{n:{'lut':n,'ff':'ff' if n=='anchor' else None} for n in c if n!='ff'},'wide_macros':[]}
        original_ff=copy.deepcopy(c['ff'])
        self.assertEqual(recover_dead_logic(d,layout),['dead'])
        self.assertEqual(c['ff'],original_ff)
        self.assertIn('anchor',c);self.assertIn('kept',c);self.assertIn('state_d',c)


if __name__=='__main__':unittest.main()
