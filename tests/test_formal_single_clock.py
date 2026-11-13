import copy,json,sys
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from formal_single_clock import audit,audit_normalized
from yosys_runtime import run,quote


def fixture():
    return {'ports':{'clk':{'direction':'input','bits':[2]},'d':{'direction':'input','bits':[3]},'q':{'direction':'output','bits':[4]}},
            'netnames':{'q':{'bits':[4],'attributes':{'init':'1'}}},
            'cells':{'ff':{'type':'$dff','parameters':{'CLK_POLARITY':'1'},'connections':{'CLK':[2],'D':[3],'Q':[4]},'port_directions':{'CLK':'input','D':'input','Q':'output'}}}}


def test_exact_positive_primary_clock_profile():
    assert audit(fixture())['defined_initial_bits']==1


@pytest.mark.parametrize('bad',['negative','second_clock','async','latch','clock_as_data','driven_clock','opaque','inout'])
def test_unsupported_clock_or_state_contract_rejects(bad):
    m=fixture();c=m['cells']['ff']
    if bad=='negative':c['parameters']['CLK_POLARITY']='0'
    if bad=='second_clock':
        m['cells']['ff2']=copy.deepcopy(c);m['cells']['ff2']['connections']['CLK']=[5]
    if bad=='async':c['type']='$adff'
    if bad=='latch':c['type']='$dlatch'
    if bad=='clock_as_data':c['connections']['D']=[2]
    if bad=='driven_clock':c['connections']['CLK']=[9]
    if bad=='opaque':m['cells']['macro']={'type':'UNKNOWN'}
    if bad=='inout':m['ports']['d']['direction']='inout'
    with pytest.raises(ValueError):audit(m)


def test_actual_enable_reset_normalization_preserves_state_identity_and_init(tmp_path):
    src=tmp_path/'model.v';src.write_text("module top(input clk,en,rst,d,output reg q); initial q=1'b1; always @(posedge clk) if(en) if(rst) q<=0; else q<=d; endmodule\n")
    run(['read_verilog -formal '+quote(src),'hierarchy -top top','proc','opt','write_json '+quote(tmp_path/'original.json'),
         'dffunmap','formalff -clk2ff','check -assert','write_json '+quote(tmp_path/'normalized.json')],tmp_path,'normalize',20)
    original=json.loads((tmp_path/'original.json').read_text())['modules']['top']
    normalized=json.loads((tmp_path/'normalized.json').read_text())['modules']['top']
    assert audit_normalized(original,normalized)['defined_initial_bits_preserved']==1
    mutated=copy.deepcopy(normalized)
    for w in mutated['netnames'].values():
        if 'init' in w.get('attributes',{}):w['attributes']['init']='0'
    with pytest.raises(ValueError,match='INIT'):audit_normalized(original,mutated)
