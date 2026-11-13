import sys,copy
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from prepare_logic_replica import reuse_existing
from native_boundary_cec import prove


def test_reuse_is_zero_area_but_requires_actual_function_equivalence(tmp_path):
    def lut(i,o):return dict(type='LUT1',attributes={},parameters={'INIT':'10'},port_directions={'I0':'input','F':'output'},connections={'I0':[i],'F':[o]})
    d={'modules':{'top':dict(cells={'a':lut(2,3),'b':lut(2,4),'sink':lut(3,5)},ports={'i':dict(direction='input',bits=[2]),'o':dict(direction='output',bits=[5])},netnames={})}}
    p={'clusters':{n:dict(lut=n,ff=None,row=0,column=0,slot=i) for i,n in enumerate(['a','b','sink'])}}
    new,layout,r=reuse_existing(d,p,'a','b',[('sink','I0')])
    assert layout==p and len(new['modules']['top']['cells'])==3 and r['equivalence_pending']
    assert all(x['cec']['equivalent'] for x in prove(d,new,tmp_path/'good',30))
    bad=copy.deepcopy(d);bad['modules']['top']['cells']['b']['parameters']['INIT']='01'
    new,_,_=reuse_existing(bad,p,'a','b',[('sink','I0')])
    with pytest.raises(ValueError):prove(bad,new,tmp_path/'bad',30)
    with pytest.raises(ValueError):reuse_existing(d,p,'a','b',[('b','I0')])
