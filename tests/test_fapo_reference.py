from pathlib import Path
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
from fapo_reference import clean,timing,minimum_copy_cut,replicate,inline,bundle,optimize,write_blif
from gowin_blif import read_blif
from run_abc_reproduction import cec


def parallel(shared=False):
    return clean(dict(inputs=['x'] if shared else ['x','z'],
        ports=['o'+str(i) for i in range(8)],outputs=['v']*4+['w']*4,
        nodes={'v':dict(inputs=['x'],truth=2,root='v'),
               'w':dict(inputs=['x' if shared else 'z'],truth=1,root='w')}),4)


def evaluate(state,values):
    values=dict(values)
    for name in state['order']:
        node=state['nodes'][name]
        index=sum(values[p]<<i for i,p in enumerate(node['inputs']))
        values[name]=(node['truth']>>index)&1
    return [values[p] for p in state['outputs']]


class FapoTest(unittest.TestCase):
    def test_minimum_cut_covers_both_tied_branches(self):
        state=parallel()
        self.assertEqual(set(minimum_copy_cut(state,1)),{'v','w'})
        self.assertEqual(timing(state,1)['delay'],4)
        selected,actions=bundle(state,4,1,4)
        self.assertEqual(actions[0]['cover'],['v','w'])
        self.assertEqual(timing(selected,1)['delay'],3)
        self.assertEqual(timing(selected,1)['area'],4)

    def test_upstream_loading_can_cancel_copy_gain(self):
        state=parallel(shared=True)
        selected,actions=bundle(state,4,1,4)
        self.assertEqual(actions,[])
        self.assertEqual(timing(selected,1)['delay'],5)

    def test_inline_truth_and_copy_preserve_function(self):
        state=clean(dict(inputs=['a','b','c'],ports=['q'],outputs=['q'],nodes={
            'p':dict(inputs=['a','b'],truth=6,root='p'),
            'q':dict(inputs=['p','c'],truth=8,root='q')}),4)
        selected=inline(state,'q','p',4)
        self.assertEqual(timing(selected,0)['area'],1)
        for x in range(8):
            inputs={v:(x>>i)&1 for i,v in enumerate(['a','b','c'])}
            self.assertEqual(evaluate(state,inputs),evaluate(selected,inputs))
        copies,_=replicate(parallel(),'v',1)
        copies=clean(copies,4)
        for x in range(4):
            inputs={'x':x&1,'z':(x>>1)&1}
            self.assertEqual(evaluate(parallel(),inputs),evaluate(copies,inputs))

    def test_seed_and_final_area_bound(self):
        for beta in [0,.05,1]:
            selected,result=optimize(parallel(),beta=beta,sweeps=2)
            self.assertLessEqual(result['selected']['area'],result['area_cap'])
            self.assertLessEqual(result['selected']['atp'],result['seed']['atp'])
        with self.assertRaises(ValueError):
            optimize(parallel(),sweeps=-1)

    def test_serialization_equivalence(self):
        binary=ROOT/'build/abc/_TEST/abc.exe'
        if not binary.exists():self.skipTest('ABC not built')
        with tempfile.TemporaryDirectory() as temp:
            folder=Path(temp);original=folder/'original.blif';target=folder/'target.blif'
            state=parallel();selected,_=optimize(state,beta=1,sweeps=2)
            original.write_text(write_blif(state));target.write_text(write_blif(selected))
            read_blif(target)
            self.assertTrue(cec(binary,original,target,folder/'cec.log',30)['equivalent'])


if __name__=='__main__':unittest.main()
