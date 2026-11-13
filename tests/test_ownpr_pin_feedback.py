import copy,sys
from pathlib import Path
import pytest
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'));sys.path.insert(0,str(ROOT/'experiments'))
from ownpr_pin_feedback import load,logical_budgets,physical_budgets
from ownpr_image_storage import load_json
from analyze_native_pin_feedback import analyze

CAPTURE=ROOT/'results/native_pin_feedback_001';STA=ROOT/'results/vexriscv_54_0mhz_001/export/native'

def test_real_internal_pins_have_distinct_slack_and_all_terminals_match():
    budgets,info=load(CAPTURE,STA)
    assert info['endpoint_matches']=={'Setup':2750,'Hold':2750}
    assert budgets['ng_coopt_2015','I0']['setup']>7
    assert budgets['ng_coopt_2015','I1']['setup']<.04
    assert budgets['ng_coopt_2015','I1']['hold']>2.7
    assert ('$auto$ff.cc:337:slice$47637','RESET') not in budgets

def test_internal_query_matches_worst_through_pin_path_arrivals():
    result=analyze(CAPTURE)
    for kind,expected in [('Setup',8595),('Hold',10005)]:
        row=result['internal_report_crosscheck'][kind]
        assert row['counts']['arrival_matched_1ps']==row['counts']['worst_through_pin_rows']==expected
        assert not row['bad_slack_rows'] and row['report_table_unchanged']

def test_sentinels_duplicates_and_shared_physical_receivers():
    rows=[dict(instance='a',pin='I0',selected_1=4.,selected_0=.8),dict(instance='b',pin='I0',selected_1=.3,selected_0=.2)]
    budgets=logical_budgets(rows)
    class Device:names={1:'X'}
    class Graph:
        device=Device()
        def node(self,r,c,n):return 7
    captures=[dict(instance=r['instance'],pin='I0',direction='In',nodes=[dict(node=[1,1,1])]) for r in rows]
    merged=physical_budgets(Graph(),captures,budgets)[7]
    assert merged['setup']==pytest.approx(.299) and merged['hold']==pytest.approx(.19)
    missing=copy.deepcopy(rows);missing[1]['selected_0']=1e10
    assert physical_budgets(Graph(),captures,logical_budgets(missing))=={}
    with pytest.raises(ValueError,match='Duplicate'):logical_budgets(rows+rows[:1])

def test_query_side_effect_control_reports_match_after_normalization():
    import re
    from ownpr_image_storage import read_text
    control=ROOT/'results/native_pin_feedback_control_001'
    assert load_json(control/'manifest.json')['control_without_queries']
    norm=lambda t:re.sub(r'^<(?:Created Time|Design File|Physical Constraints File|Timing Constraints File)>:.*\n','',t,flags=re.M)
    for name in ('report_before.tr','report_after.tr'):
        assert norm(read_text(CAPTURE/name))==norm(read_text(control/name))
