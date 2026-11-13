import sys
from pathlib import Path
import pytest
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from rollback_ownpr_carry_neighbors import implicated

def test_only_violated_data_paths_trigger_rollback_including_negative_zero():
    text='''3.3.1 Setup Analysis Report
Path1
Slack : -0.100
Data Arrival Path:
own_i_1/I0
Data Required Path:
own_i_2/CLK
Path2
Slack : 0.100
Data Arrival Path:
own_i_2/I0
Data Required Path:
3.3.2 Hold Analysis Report
Path1
Slack : -0.000
Data Arrival Path:
own_i_3/I1
Data Required Path:
'''
    mapping={'instances':{'a':'own_i_1','b':'own_i_2','c':'own_i_3'}}
    assert implicated(text,mapping,{'a','b','c'})=={'a','c'}
    with pytest.raises(ValueError):implicated('',mapping,{'a'})
