import copy,sys
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from audit_native_ioreg_contract import expected_attributes
from ownpr_ioreg_configuration import output_attributes

def reg():
    return dict(initial_value="1'b0",inverted_clock=False,pins=[dict(pin='r.'+p,source_class='.?AVVcc@NETLIST@@' if p=='CE' else '.?AVGnd@NETLIST@@' if p=='RESET' else '.?AVLut4@NETLIST@@',inversion_annotation=False) for p in ('D','Q','CLK','CE','RESET')])

def test_declared_init_and_constant_controls_are_preserved():
    attrs,s=expected_attributes(reg());assert attrs==output_attributes('DFFRE','one','zero')
    assert s['initial']==0 and s['ce']=='one' and s['control']=='zero'

@pytest.mark.parametrize('bad',['init','unknown_init','duplicate','data_inversion','unresolved_inverter'])
def test_unsupported_or_changed_semantics_reject(bad):
    r=reg()
    if bad=='init':r['initial_value']="1'b1"
    elif bad=='unknown_init':r['initial_value']="1'bx"
    elif bad=='duplicate':r['pins'].append(copy.deepcopy(r['pins'][0]))
    elif bad=='data_inversion':r['pins'][0]['inversion_annotation']=True
    else:r['pins'][2]['source_class']='.?AVInv@NETLIST@@';r['pins'][3]['source_class']='.?AVInv@NETLIST@@'
    with pytest.raises(ValueError):expected_attributes(r)

def test_dynamic_control_polarity_and_preset_initial_state():
    r=reg();r['initial_value']="1'h1";p=r['pins'][-1];p.update(pin='r.PRESET',source_class='.?AVIobuf@NETLIST@@',inversion_annotation=True)
    attrs,s=expected_attributes(r)
    assert attrs==output_attributes('DFFPE','one','inverted') and s['initial']==1
