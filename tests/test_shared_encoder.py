import random
import sys
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from ownpr_shared_encoder import canonical,emit_function,sink_frontier,exit_frontier
from ownpr_multioutput_acd import decompose_many,verify_many
from analyze_large_cut_acd import candidates
from ownpr_multilevel_cut import Cuts
from ownpr_mux_macros import tree


class SharedEncoderTests(unittest.TestCase):
    def test_canonical_order_preserves_asymmetric_function(self):
        # output is the first input AND NOT the second, not symmetric.
        bits,truth=canonical([7,2],0b0010)
        self.assertEqual(bits,(2,7));self.assertEqual(truth,0b0100)

    def test_duplicate_variables_and_constants_reduce(self):
        # XOR of repeated variable is constant zero.
        self.assertEqual(canonical([9,9],0b0110),((),0))
        self.assertEqual(canonical([9,'1'],0b1000),((9,),0b10))

    def test_structural_eight_input_cone_reduces_directly(self):
        truth=sum((((a&1)^((a>>1)&1)))<<a for a in range(256))
        count,options=candidates(truth)
        self.assertEqual(count,1);self.assertTrue(options[0]['direct_function'])
        self.assertEqual(options[0]['free'],[0,1]);self.assertEqual(options[0]['leaves'],[])

    def test_seven_input_encoder_fallback_covers_random_eight_input_truth(self):
        truth=random.Random(818).getrandbits(256)
        count,options=candidates(truth,max_encoder=7)
        self.assertGreater(count,0)
        verify_many([options[0]],[truth])

    def test_frontier_excludes_ancestors_across_parallel_sinks(self):
        rows={'a':{'upstream_cells':['a']},'b':{'upstream_cells':['a','b']},'c':{'upstream_cells':['a','c']}}
        self.assertEqual(set(sink_frontier(rows)),{'b','c'})

    def test_exit_frontier_preserves_escaping_branch(self):
        def cell(i,o):return dict(type='LUT1',parameters={'INIT':'10'},connections={'I0':[i],'F':[o]},port_directions={'I0':'input','F':'output'})
        d={'modules':{'top':{'cells':{'a':cell(1,2),'b':cell(2,3),'escape':cell(2,4)},'ports':{'out':{'direction':'output','bits':[3,4]}}}}}
        self.assertEqual(set(exit_frontier(d,{'a':{},'b':{}})),{'a','b'})
        del d['modules']['top']['cells']['escape'];d['modules']['top']['ports']['out']['bits']=[3]
        self.assertEqual(set(exit_frontier(d,{'a':{},'b':{}})),{'b'})

    def test_multioutput_encoders_reconstruct_all_assignments(self):
        truths=[sum(((a&7).bit_count()%2 ^ ((a>>3)&1))<<a for a in range(256)),
                sum((((a&7).bit_count()%2)&((a>>4)&1))<<a for a in range(256))]
        cs=decompose_many(truths,8,7,(0,1,2),(5,))
        self.assertIsNotNone(cs);self.assertEqual(cs[0]['leaves'],cs[1]['leaves']);verify_many(cs,truths)
        bad=[dict(c) for c in cs];bad[0]['root']^=1
        with self.assertRaises(ValueError):verify_many(bad,truths)

    def test_emitted_native_lut_network_matches_random_functions(self):
        rng=random.Random(2711)
        for width in range(1,8):
            for trial in range(8):
                truth=rng.getrandbits(1<<width)
                if truth in (0,(1<<(1<<width))-1):continue
                inputs=list(range(1,width+1));d={'modules':{'top':{'cells':{},'ports':{},'netnames':{}}}}
                layout={'clusters':{},'mux_clusters':{},'wide_macros':[]};nextbit=100
                def allocate():
                    nonlocal nextbit
                    nextbit+=1;return nextbit
                added=emit_function(d,layout,'result',inputs,truth,90,(20,20),'0',allocate,lambda b:(-b,b),loads={b:b*3 for b in inputs} if trial%2 else None)
                graph=Cuts(d,k=7);cut=graph.function('result',inputs)
                expected=canonical(inputs,truth)
                self.assertEqual((tuple(cut['cut_bits']),int(cut['truth_hex'],16)),expected)
                self.assertEqual(len(set(added)),len(added))
                if layout['wide_macros']:
                    spec=tree(d,'result');self.assertEqual(set(spec['leaves']),set(layout['clusters']))
                    self.assertEqual(len({r['slot'] for r in layout['clusters'].values()}),len(layout['clusters']))

    def test_emit_rejects_unmodeled_eight_input_function(self):
        d={'modules':{'top':{'cells':{}}}}
        with self.assertRaises(ValueError):
            emit_function(d,{},'x',range(8),1,90,(0,0),'0',lambda:1,lambda b:b)


if __name__=='__main__':unittest.main()
