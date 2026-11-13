import copy,sys,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from ownpr_multilevel_coopt import select

def option(root,interior,paths,slack=0.,gain=2.,growth=3):
    return dict(cut=dict(root=root,interior=interior),witness_paths=paths,worst_slack_ns=slack,priority_proxy_ns={i:gain for i in paths},lut_growth=growth)

class MultilevelSelectionTests(unittest.TestCase):
    def test_long_path_requires_multiple_disjoint_cuts(self):
        rows=[option('a',['a','b','c'],[0],gain=2),option('d',['d','e','f'],[0],gain=2),option('g',['g','h','i'],[1],gain=1)]
        chosen,report=select(rows,{0,1},count=3,max_growth=9,required_gain={0:3.5,1:1.})
        self.assertEqual([r['cut']['root'] for r in chosen],['a','d','g'])
        self.assertEqual(report['uncovered_paths'],[]);self.assertEqual(report['accumulated_priority_proxy_ns'][0],4.)
        chosen,report=select(rows,{0,1},count=1,required_gain={0:3.5,1:1.})
        self.assertEqual(report['covered_paths'],[]);self.assertEqual(report['remaining_gain_proxy_ns'][0],1.5)

    def test_selects_competing_paths_with_full_interior_conflict_checks(self):
        rows=[option('a',['a','b','c'],[0,1]),option('d',['d','b','e'],[2],gain=20),option('f',['f','g','h'],[2],slack=.1),option('i',['i','j','k'],[0,1],gain=1)]
        before=copy.deepcopy(rows);chosen,report=select(rows,{0,1,2},count=3,max_growth=6)
        self.assertEqual([r['cut']['root'] for r in chosen],['a','f']);self.assertEqual(report['uncovered_paths'],[]);self.assertEqual(rows,before)

    def test_uncovered_paths_and_area_limits_are_not_hidden(self):
        rows=[option('a',['a','b','c'],[0]),option('d',['d','e','f'],[1],growth=4)]
        chosen,report=select(rows,{0,1,2},max_growth=3)
        self.assertEqual(len(chosen),1);self.assertEqual(report['uncovered_paths'],[1,2]);self.assertEqual(report['lut_growth'],3)
        with self.assertRaises(ValueError):select(rows,{0},count=0)

if __name__=='__main__':unittest.main()
