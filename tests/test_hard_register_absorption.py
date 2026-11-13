"""Reject structurally unsafe DSP register-absorption candidates."""
import copy
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from analyze_hard_register_absorption import analyze


def fixture():
    cells={
        'dsp':{'type':'MULTALU27X18','parameters':{'OREG_CLK':'BYPASS'},'connections':{'DOUT':[10]},'port_directions':{'DOUT':'output'}},
        'hold':{'type':'LUT3','parameters':{'INIT':'11001010'},'connections':{'I0':[12],'I1':[10],'I2':[4],'F':[11]},'port_directions':{'I0':'input','I1':'input','I2':'input','F':'output'}},
        'ff':{'type':'DFFRE','parameters':{'INIT':'0'},'connections':{'D':[11],'Q':[12],'CLK':[2],'CE':['1'],'RESET':['0']},'port_directions':{'D':'input','Q':'output','CLK':'input','CE':'input','RESET':'input'}}}
    return {'modules':{'top':{'cells':cells}}}


def test_feedback_mux_yields_candidate_with_preserved_controls():
    r=analyze(fixture());assert r['candidate_ff_count']==1
    group=r['banks'][0]['control_groups'][0]['controls']
    assert group=={'clock':2,'reset':'0','effective_enable_and':[4],'init':'0'}
    assert r['banks'][0]['rewrite_admitted'] is False


def test_not_feedback_and_wrong_lut_function_reject():
    for mutation in ('feedback','function'):
        d=fixture();c=d['modules']['top']['cells']['hold']
        if mutation=='feedback':c['connections']['I0']=[99]
        else:c['parameters']['INIT']='10101010'
        r=analyze(d);assert r['candidate_ff_count']==0
        assert len(r['banks'][0]['rejected_used_bits'])==1


def test_shared_dsp_output_is_not_private_absorption_candidate():
    d=fixture();c=d['modules']['top']['cells'];c['extra']=copy.deepcopy(c['ff']);c['extra']['connections']['D']=[10]
    assert analyze(d)['candidate_ff_count']==0


def test_ff_enable_is_conjoined_not_discarded():
    d=fixture();d['modules']['top']['cells']['ff']['connections']['CE']=[8]
    r=analyze(d);assert r['banks'][0]['control_groups'][0]['controls']['effective_enable_and']==[4,8]


def test_top_observable_combinational_output_cannot_be_absorbed():
    d=fixture();d['modules']['top']['ports']={'visible':{'direction':'output','bits':[10]}}
    assert analyze(d)['candidate_ff_count']==0
