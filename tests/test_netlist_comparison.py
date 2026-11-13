"""Check distinctions needed for trustworthy structural comparisons."""
import sys
from pathlib import Path
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from compare_netlist_structure import features


class ComparisonTests(unittest.TestCase):
    def test_ff_copy_groups_preserve_control_and_initial_state_distinctions(self):
        def ff(q,reset=5):return dict(type='DFFRE',parameters={'INIT':'0'},connections=dict(D=[2],CLK=[3],CE=[4],RESET=[reset],Q=[q]))
        top=dict(attributes={},ports={},netnames=dict(
            a=dict(bits=[10],attributes={'init':'0'}),b=dict(bits=[11],attributes={'init':'0'}),
            c=dict(bits=[12],attributes={'init':'1'})),
            cells=dict(a=ff(10),b=ff(11),c=ff(12),d=ff(13,6)))
        report=features(dict(modules=dict(top=top)),'top')
        self.assertEqual(report['same_input_ff_extra_cells'],1)
        self.assertEqual(report['same_input_ff_groups'][0]['names'],['a','b'])

    def test_local_carry_counts_are_weighted_by_hierarchy_multiplicity(self):
        def alu(ci,co):return dict(type='ALU',parameters={'ALU_MODE':'10'},connections=dict(CIN=[ci],COUT=[co]))
        child=dict(attributes={},cells=dict(a=alu(2,3),b=alu(3,4),c=alu(5,6)))
        top=dict(attributes={},cells=dict(x=dict(type='child'),y=dict(type='child')))
        report=features(dict(modules=dict(top=top,child=child)),'top')
        self.assertEqual(report['local_carry_chain_lengths'],{'1':2,'2':2})
        self.assertEqual(report['resources']['leaf_cells']['ALU'],6)


if __name__=='__main__':unittest.main()
