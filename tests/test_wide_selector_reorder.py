import copy,sys
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from ownpr_wide_selector_reorder import transform
from ownpr_multilevel_cut import Cuts


def fixture(width):
    truth=0x6fe2a7319c58d04b&((1<<(1<<width))-1);cells={}
    def cell(kind,connections,params=None):return dict(type=kind,connections={k:[v] for k,v in connections.items()},parameters=params or {},attributes={},port_directions={k:'output' if k in ('F','O') else 'input' for k in connections})
    for i in range(1<<(width-4)):
        cells['leaf'+str(i)]=cell('LUT4',{**{'I'+str(j):j+2 for j in range(4)},'F':100+i},{'INIT':format((truth>>(16*i))&65535,'016b')})
    if width==5:cells['root']=cell('MUX2_LUT5',dict(I0=100,I1=101,S0=6,O=200))
    else:
        cells['a']=cell('MUX2_LUT5',dict(I0=100,I1=101,S0=6,O=150))
        cells['b']=cell('MUX2_LUT5',dict(I0=102,I1=103,S0=6,O=151))
        cells['root']=cell('MUX2_LUT6',dict(I0=150,I1=151,S0=7,O=200))
    return dict(modules={'top':dict(cells=cells,netnames={},ports={'a':dict(direction='input',bits=list(range(2,width+2))),'y':dict(direction='output',bits=[200])})}),truth


@pytest.mark.parametrize('width',[5,6])
def test_all_nonroot_variables_preserve_known_truth_and_topology(width):
    design,truth=fixture(width);snapshot=copy.deepcopy(design)
    for bit in range(2,width+1):
        changed,r=transform(design,'root',bit)
        assert design==snapshot and r['exhaustive_vectors']==1<<width
        assert changed['modules']['top']['cells']['root']['connections']['S0']==[bit]
        assert int(Cuts(changed).function('root',set(range(2,width+2)))['truth_hex'],16)==truth
        assert changed['modules']['top']['ports']==design['modules']['top']['ports']
        for name,cell in design['modules']['top']['cells'].items():
            other=changed['modules']['top']['cells'][name]
            assert cell['type']==other['type']
            for pin in ('F','O','I0','I1'):
                if pin in cell['connections'] and (pin in ('F','O') or cell['type'].startswith('MUX')):assert cell['connections'][pin]==other['connections'][pin]


def test_observed_or_kept_internal_nodes_and_noop_are_rejected():
    d,_=fixture(6);d['modules']['top']['ports']['probe']=dict(direction='output',bits=[100])
    with pytest.raises(ValueError,match='observed'):transform(d,'root',2)
    d,_=fixture(6);d['modules']['top']['cells']['leaf0']['attributes']['keep']='1'
    with pytest.raises(ValueError,match='Kept'):transform(d,'root',2)
    d,_=fixture(6)
    with pytest.raises(ValueError,match='already'):transform(d,'root',7)
    with pytest.raises(ValueError,match='support'):transform(d,'root',99)
