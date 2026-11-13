import sys
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from compare_endpoint_timing import compare


def test_changed_source_is_not_reported_as_identical_path_improvement():
    a={'ff/D':dict(source='a',slack_ns=-1.,cell_ns=2.,wire_ns=5.,nodes=['a/Q','ff/D'])}
    b={'ff/D':dict(source='b',slack_ns=-.5,cell_ns=3.,wire_ns=3.5,nodes=['b/Q','ff/D'])}
    row=compare(a,b)[0]
    assert row['source_changed'] and row['path_changed']
    assert row['slack_gain_ns']==.5 and row['cell_delta_ns']==1. and row['wire_delta_ns']==-1.5
    with pytest.raises(ValueError,match='disappeared'):compare(a,{})
