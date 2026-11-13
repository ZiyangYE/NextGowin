import copy,sys
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from ownpr_timing_cut_remap import compose


def lut(bits,out,word):
    k=len(bits);return dict(type='LUT'+str(k),parameters={'INIT':format(word,f'0{1<<k}b')},attributes={},
        port_directions={**{f'I{i}':'input' for i in range(k)},'F':'output'},connections={**{f'I{i}':[b] for i,b in enumerate(bits)},'F':[out]})


def evaluate(cell,values):
    k=int(cell['type'][-1]);return int(cell['parameters']['INIT'],2)>>sum(values[cell['connections'][f'I{i}'][0]]<<i for i in range(k))&1


def test_every_two_input_parent_and_root_function_matches_for_all_boundary_values():
    for a in range(16):
        for b in range(16):
            d={'modules':{'top':{'cells':{'a':lut([1,2],4,a),'b':lut([4,3],5,b),'other':lut([4],6,2)}}}}
            before=copy.deepcopy(d)
            try:new,info=compose(d,'a','b')
            except ValueError as e:
                assert any(s in str(e) for s in ('dependent','fit','Constant'));continue
            assert d==before and new['modules']['top']['cells']['a']==d['modules']['top']['cells']['a']
            assert new['modules']['top']['cells']['other']==d['modules']['top']['cells']['other']
            for v in range(8):
                values={i+1:v>>i&1 for i in range(3)};values[4]=evaluate(d['modules']['top']['cells']['a'],values)
                assert evaluate(new['modules']['top']['cells']['b'],values)==evaluate(d['modules']['top']['cells']['b'],values)
            assert info['lut_growth']==0 and info['output_bit']==5


def test_duplicate_inputs_and_constants_are_folded_without_assuming_correlations():
    d={'modules':{'top':{'cells':{'a':lut([1,1],4,6),'b':lut([4,2,'1'],5,0x96)}}}}
    new,info=compose(d,'a','b')
    for v in range(4):
        values={1:v&1,2:v>>1,'1':1};values[4]=evaluate(d['modules']['top']['cells']['a'],values)
        assert evaluate(new['modules']['top']['cells']['b'],values)==evaluate(d['modules']['top']['cells']['b'],values)
    assert info['boundary_bits']==[2]


def test_oversized_or_cyclic_cuts_are_rejected():
    d={'modules':{'top':{'cells':{'a':lut([1,2,3,4],8,0x6996),'b':lut([8,5,6],9,0x96)}}}}
    with pytest.raises(ValueError,match='fit'):compose(d,'a','b')
    d['modules']['top']['cells']['a']=lut([9,1],8,6)
    with pytest.raises(ValueError,match='acyclic'):compose(d,'a','b')
