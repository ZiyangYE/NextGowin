import copy
import itertools
from pathlib import Path
import sys
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from lower_carry_only import lower


def fixture(mode=2):
    cell=dict(type='ALU',parameters={'ALU_MODE':format(mode,'032b')},attributes={},
              connections={p:[b] for p,b in dict(I0=2,I1=3,I3=4,CIN=5,COUT=6,SUM=7).items()},
              port_directions={p:'output' if p in ('SUM','COUT') else 'input' for p in ('I0','I1','I3','CIN','COUT','SUM')})
    return dict(modules={'top':dict(attributes={},cells={'alu':cell},netnames={},
                                   ports={'carry':dict(direction='output',bits=[6])})})


class CarryOnlyTests(unittest.TestCase):
    def test_boolean_lowering_truth_and_immutable_checkpoint(self):
        for mode in (0,1,2):
            source=fixture(mode);saved=copy.deepcopy(source);d,r=lower(source)
            self.assertEqual(source,saved);self.assertEqual(r['alu_cells_removed'],1)
            for a,b,s,c in itertools.product((0,1),repeat=4):
                values={2:a,3:b,4:s,5:c,'0':0,'1':1}
                for cell in d['modules']['top']['cells'].values():
                    p={p:values[bits[0]] for p,bits in cell['connections'].items() if p!='Y'}
                    if cell['type']=='$_XOR_':y=p['A']^p['B']
                    elif cell['type']=='$_NOT_':y=1-p['A']
                    elif cell['type']=='$_MUX_':y=p['B'] if p['S'] else p['A']
                    else:self.fail('Unexpected gate')
                    values[cell['connections']['Y'][0]]=y
                control=1 if mode==0 else 0 if mode==1 else s
                propagate=a^b^(1-control)
                self.assertEqual(values[6],c if propagate else a)

    def test_used_or_kept_sum_and_unknown_mode_retain_entire_chain(self):
        source=fixture();m=source['modules']['top'];second=copy.deepcopy(m['cells']['alu'])
        second['connections'].update(CIN=[6],COUT=[8],SUM=[9]);m['cells']['next']=second
        m['ports']['carry']['bits']=[8]
        for reason in ('exported','kept','consumed','mode'):
            d=copy.deepcopy(source);m=d['modules']['top']
            if reason=='exported':m['ports']['sum']=dict(direction='output',bits=[9])
            elif reason=='kept':m['netnames']['sum']=dict(bits=[9],attributes={'keep':'1'})
            elif reason=='consumed':m['cells']['next']['connections']['I0']=[7]
            else:m['cells']['next']['parameters']['ALU_MODE']='1111'
            after,r=lower(d);self.assertEqual(r['alu_cells_removed'],0);self.assertEqual(after,d)


if __name__=='__main__':unittest.main()
