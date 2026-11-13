import sys
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from analyze_ownpr_frequency_barrier import barriers


def test_competing_launch_groups_limit_single_cone_improvement():
    paths=[dict(path=1,source='a',slack_ns=0.,data_wire_ns=6.),
           dict(path=2,source='b',slack_ns=.1,data_wire_ns=2.),
           dict(path=3,source='a',slack_ns=3.,data_wire_ns=3.)]
    data=barriers(paths,10.,[125.])
    row=data['targets'][0]
    assert row['reported_paths_needing_relief']==2
    assert row['worst_required_relief_ns']==pytest.approx(2.)
    assert row['groups'][0]['unaffected_path_ceiling_mhz']==pytest.approx(1000/9.9)
    # Removing every wire still leaves the second path's cell/constraint cost.
    ideal=data['uniform_wire_counterfactuals'][-1]
    assert ideal['limiting_reported_path']==2
    assert ideal['period_ns']==pytest.approx(7.9)


def test_met_target_has_zero_deficit():
    p=[dict(path=1,source='a',slack_ns=1.,data_wire_ns=2.)]
    row=barriers(p,10.,[100.])['targets'][0]
    assert row['reported_paths_needing_relief']==0
    assert row['groups']==[]
