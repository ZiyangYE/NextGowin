import json,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from compare_reset_path_reports import paths

def test_aliases_and_hold_sections_do_not_change_setup_identity(tmp_path):
    (tmp_path/'name_map.json').write_text(json.dumps({'instances':{'reset_pad':'own_i_9','state':'own_i_3'}}))
    def block(slack,arrival):
        return f'Path Summary:\nSlack : {slack}\nData Arrival Time : {arrival}\nFrom : own_i_9\nTo : own_i_3\nData Arrival Path:\n 8.0 7.5 tNET RR 1 R1 own_i_3/D\nData Required Path:\n'
    (tmp_path/'probe.tr').write_text('3.3.1 Setup Analysis Report\n'+block(-1,8)+'3.3.2 Hold Analysis Report\n'+block(-10,1))
    result=paths(tmp_path,'reset_pad')
    assert list(result)==['state']
    assert result['state']['arrival_ns']==8
    assert result['state']['slack_ns']==-1
    assert result['state']['arcs'][0]['delay_ns']==7.5
