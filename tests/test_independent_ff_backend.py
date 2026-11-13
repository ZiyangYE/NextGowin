import copy,sys
from pathlib import Path
from types import SimpleNamespace
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import ownpr_independent_ff_inputs as modes
import ownpr_device
from ownpr_ff_configuration import compile_initial


def fixture(monkeypatch):
    monkeypatch.setattr(modes,'calibration',lambda:'checked')
    device=SimpleNamespace(rows=2,cols=2,grid=[[17,17],[17,17]])
    monkeypatch.setattr(ownpr_device,'Device',lambda:device)
    cells={'lut':dict(type='LUT1',parameters={'INIT':'10'},connections={'I0':[10],'F':[20]}),
        'ff':dict(type='DFFRE',parameters={'INIT':'0'},connections={'CLK':[1],'CE':[2],'RESET':[3],'D':[10],'Q':[12]}),
        'gnd':dict(type='GND',connections={'G':[3]}),'vcc':dict(type='VCC',connections={'V':[2]})}
    design=dict(modules={'top':dict(cells=cells)})
    placed=dict(clusters={'lut':dict(lut='lut',ff='ff',row=0,column=0,slot=0,independent_ff_input=True)},independent_ff_calibration='checked')
    return design,placed,device


def test_independent_input_is_not_a_zero_wire_paired_edge(monkeypatch):
    design,placed,_=fixture(monkeypatch);result=compile_initial(design,placed)
    assert result['paired_data_connections']==[]
    assert result['independent_data_inputs'][0]['selector']=='SEL0'
    assert result['sites'][0]['groups'][0][11:13]==[1,2]
    assert result['sites'][0]['groups'][0][13:15]==[17,17]


@pytest.mark.parametrize('bad',['missing_calibration','wide_overlap','shared_group','paired_data','out_of_bounds','self_feedback','constant_data'])
def test_unsupported_mode_or_resource_combination_rejects(monkeypatch,bad):
    design,placed,device=fixture(monkeypatch)
    if bad=='missing_calibration':placed.pop('independent_ff_calibration')
    elif bad=='wide_overlap':placed['mux_clusters']={'mux':dict(row=0,column=0,slot=0)}
    elif bad=='shared_group':placed['clusters']['other']=dict(lut='other',ff='ff2',row=0,column=0,slot=1)
    elif bad=='paired_data':design['modules']['top']['cells']['ff']['connections']['D']=[20]
    elif bad=='self_feedback':design['modules']['top']['cells']['ff']['connections']['D']=[12]
    elif bad=='constant_data':design['modules']['top']['cells']['ff']['connections']['D']=[3]
    else:placed['clusters']['lut']['row']=-1
    with pytest.raises(ValueError):modes.validate(design,placed,device)


def test_unmarked_nonpaired_data_still_rejects(monkeypatch):
    design,placed,_=fixture(monkeypatch);placed['clusters']['lut'].pop('independent_ff_input')
    with pytest.raises(ValueError,match='not its paired LUT'):compile_initial(design,placed)
