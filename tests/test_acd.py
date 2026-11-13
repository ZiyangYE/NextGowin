import importlib.util
from pathlib import Path
import random
import unittest
spec = importlib.util.spec_from_file_location('acd', Path(__file__).resolve().parents[1]/'scripts/acd_reference.py')
acd = importlib.util.module_from_spec(spec)
spec.loader.exec_module(acd)


class ACDTest(unittest.TestCase):
    def test_late_input_changes_optimal_partition(self):
        tt = sum((((x&31).bit_count()%2)&((x>>5)&1))<<x for x in range(64))
        candidates = acd.search(tt, [0,0,0,0,0,5000])
        self.assertIn(5, candidates[0]['free'])
        self.assertEqual(candidates[0]['arrival_ps'], 6000)
        self.assertEqual(min(c['arrival_ps'] for c in candidates if 5 in c['bound']), 7000)
        other = acd.search(tt, [5000,0,0,0,0,0])
        self.assertNotEqual(candidates[0]['bound'], other[0]['bound'])
        self.assertIn(0, other[0]['free'])

    def test_shared_variable_reconstruction(self):
        rng = random.Random(21)
        for _ in range(20):
            h = rng.getrandbits(8)
            g = rng.getrandbits(8)
            tt = 0
            for x in range(32):
                b = x&3
                s = (x>>2)&1
                f = (x>>3)&3
                code = (h>>(b|(s<<2)))&1
                # Root has free x3/x4 plus code; shared dependence is within h.
                tt |= ((g>>(f|(code<<2)))&1)<<x
            c = acd.decompose(tt,5,4,(0,1),(2,))
            self.assertIsNotNone(c)
            self.assertEqual([acd.evaluate(c,x) for x in range(32)], [(tt>>x)&1 for x in range(32)])

    def test_rejects_infeasible_random_partition(self):
        # Every bound assignment has a different cofactor: encoding exceeds root capacity.
        tt = sum(((x&7)==((x>>3)&7))<<x for x in range(64))
        self.assertIsNone(acd.decompose(tt,6,4,(0,1,2)))


if __name__ == '__main__':
    unittest.main()
