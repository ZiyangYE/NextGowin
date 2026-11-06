import sys
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from ownpr_target_protection import protection_window,from_capture
from ownpr_feedback_transport import protected_placement_cells


def fixture(period=15):
    sdc=f'create_clock -name clk -period {period} [get_ports {{clk}}]\nset_input_delay -max 0 -clock clk [get_ports {{data}}]\n'
    report=f'3.1.1 Setup Paths Table\n1 2.000 a/Q b/D\n3.1.2 Hold Paths Table\n3.3.1 Setup Analysis Report\nPath1\nSetup Relationship: {period}\n3.3.2 Hold Analysis Report\n'
    return sdc,report


def test_target_period_protects_an_old_positive_slack_path_without_changing_hold():
    window,audit=protection_window(*fixture(),100,.5)
    assert window==5.5 and audit['period_reduction_ns']==5 and audit['hold_window_unchanged']
    d={'modules':{'top':{'cells':{'a':{'port_directions':{'I0':'input'}}}}}}
    p={'clusters':{'a':{'ff':None}}};budgets={('a','I0'):dict(original_setup_slack_ns=2.,original_hold_slack_ns=1.)}
    assert not protected_placement_cells(d,p,budgets,.5)[0]
    assert protected_placement_cells(d,p,budgets,window)[0]=={'a'}
    assert not audit['current_slacks_predicted']


def test_slower_target_does_not_relax_existing_incidental_protection():
    window,audit=protection_window(*fixture(),50,.5)
    assert window==.5 and audit['period_reduction_ns']==-5


@pytest.mark.parametrize('extra',['set_multicycle_path 2 -setup','set_false_path -from [get_ports data]','create_generated_clock -name other','create_clock -name other -period 5 [get_ports other]'])
def test_exceptions_and_multiple_clocks_fail_closed(extra):
    sdc,report=fixture()
    with pytest.raises(ValueError):protection_window(sdc+extra,report,100)


def test_report_coverage_and_clock_relationship_must_match():
    sdc,report=fixture()
    for bad in [report.replace('Relationship: 15','Relationship: 7.5'),report.replace('1 2.000 a/Q b/D',''),report.replace('Path1','Path1\nSetup Relationship: 15')]:
        with pytest.raises(ValueError,match='coverage'):protection_window(sdc,bad,100)
    with pytest.raises(ValueError):protection_window(sdc.replace('-clock clk','-clock another'),report,100)


