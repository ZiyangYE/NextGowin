import itertools
import random
from pathlib import Path
import sys
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from gw5a_functional_map import lower,choices,cost,blif,permute_lut4_pins


def evaluate(network,values):
    values=dict(values)
    for n in network['nodes']:
        a=sum(values[p]<<i for i,p in enumerate(n['inputs']))
        values[n['output']]=(n['truth']>>a)&1
    return [values[p] for p in network['outputs']]


class ArchitectureMappingTests(unittest.TestCase):
    def test_pin_permutation_preserves_all_assignments_and_late_input(self):
        import copy
        rng=random.Random(717)
        for _ in range(20):
            n=dict(inputs=list('abcdef'),outputs=['y','t'],nodes=[
                dict(inputs=list('abcd'),output='t',truth=rng.getrandbits(16)),
                dict(inputs=['t','c','e','f'],output='y',truth=rng.getrandbits(16))],topological=['t','y'])
            saved=copy.deepcopy(n)
            mapped,record=permute_lut4_pins(n,{'a':2000},[521,517,457,287])
            self.assertEqual(n,saved)
            self.assertEqual(mapped['nodes'][0]['inputs'][3],'a')
            self.assertTrue(record['changed'])
            for a in itertools.product((0,1),repeat=6):
                values=dict(zip(n['inputs'],a));self.assertEqual(evaluate(n,values),evaluate(mapped,values))
        with self.assertRaises(ValueError):permute_lut4_pins(n,{},[0,1,2,float('nan')])

    def test_exact_cofactors_exhaustive_inputs_for_random_functions(self):
        rng=random.Random(39017)
        for k in range(1,7):
            for _ in range(12):
                truth=rng.getrandbits(1<<k);inputs=['x'+str(i) for i in range(k)]
                n=dict(inputs=inputs,outputs=['y'],nodes=[dict(inputs=inputs,output='y',truth=truth)],topological=['y'])
                mapped,record=lower(n)
                for a in range(1<<k):self.assertEqual(evaluate(mapped,{p:(a>>i)&1 for i,p in enumerate(inputs)}),[(truth>>a)&1])
                drivers={n['output']:n for n in mapped['nodes']}
                for node in mapped['nodes']:
                    tier=node.get('native_mux')
                    if tier:
                        leaves=[drivers[p] for p in node['inputs'][:2]]
                        self.assertTrue(all(x.get('keep') for x in leaves))
                        self.assertTrue(all(x.get('native_mux')==5 for x in leaves) if tier==6 else all(x.get('native_mux') is None and len(x['inputs'])<=4 for x in leaves))

    def test_late_selector_and_output_specific_support(self):
        inputs=tuple('abcdef')
        truth=sum((((a>>((a>>4)&3))&1))<<a for a in range(64))
        plans=list(choices(inputs,truth));arrivals={p:0 for p in inputs};arrivals['f']=5000
        chosen=min(plans,key=lambda p:cost(p,arrivals))
        self.assertEqual(chosen[0],'mux5');self.assertEqual(chosen[1],'f')
        self.assertEqual(cost(chosen,arrivals)[1],2)
        self.assertLess(cost(chosen,arrivals)[0],6000)

    def test_oversized_function_is_rejected(self):
        # Parity depends on every input, so support reduction cannot hide width.
        truth=sum((i.bit_count()%2)<<i for i in range(128))
        with self.assertRaises(ValueError):list(choices(tuple(range(7)),truth))


if __name__=='__main__':unittest.main()
