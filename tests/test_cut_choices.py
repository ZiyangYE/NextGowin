import itertools
from pathlib import Path
import random
import sys
import unittest
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
from gw5a_cut_choices import optimize
from gw5a_functional_map import lower
from test_gw5a_arch_map import evaluate


class EquivalentCutTests(unittest.TestCase):
    def test_random_reconvergent_graphs_all_assignments_and_area_bound(self):
        rng=random.Random(105037)
        for trial in range(30):
            inputs=list('abcde');available=inputs[:];nodes=[]
            for i in range(12):
                ins=rng.sample(available,rng.randrange(1,min(len(available),4)+1));name='n'+str(i)
                nodes.append(dict(inputs=ins,output=name,truth=rng.getrandbits(1<<len(ins))));available.append(name)
            # Intermediate observables represent protected hard-resource roots.
            network=dict(inputs=inputs,outputs=['n11','n5','n8'],nodes=nodes,topological=[n['output'] for n in nodes])
            candidate,report=optimize(network,max_cuts=8,max_window_nodes=5)
            self.assertEqual(candidate['outputs'],network['outputs'])
            self.assertLessEqual(report['final_lut_slots'],report['cap'])
            for a in itertools.product((0,1),repeat=5):
                values=dict(zip(inputs,a));self.assertEqual(evaluate(candidate,values),evaluate(network,values))
            _,old=lower(network,cut_wire_ps=672);_,new=lower(candidate,cut_wire_ps=672)
            self.assertLessEqual(max(new['output_arrivals_ps'].values()),max(old['output_arrivals_ps'].values()))

    def test_late_selector_cut_crosses_a_shared_internal_boundary_safely(self):
        # Inverted intermediate data cofactor and remaining four inputs form
        # a complete 4:1 mux, which can absorb the extra cut at late f.
        network=dict(inputs=list('abcdef'),outputs=['y','t'],nodes=[
            dict(inputs=['a','c','f'],output='t',truth=53),
            dict(inputs=['b','d','t','f','e'],output='y',truth=sum((int((not ((a>>2)&1)) if not (a>>4&1) else ((a>>1)&1) if a>>3&1 else a&1))<<a for a in range(32)))],topological=['t','y'])
        candidate,report=optimize(network,{'f':5000},area_allowance=1)
        self.assertTrue(report['rewrites']);self.assertIn('t',{n['output'] for n in candidate['nodes']})
        for a in itertools.product((0,1),repeat=6):
            values=dict(zip(network['inputs'],a));self.assertEqual(evaluate(candidate,values),evaluate(network,values))


if __name__=='__main__':unittest.main()
