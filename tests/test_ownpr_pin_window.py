import sys
from pathlib import Path
import pytest
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from ownpr_lut_pin_eco import setup_pin_targets

def test_passing_critical_paths_can_be_optimized_without_relaxing_the_sdc():
    report='''3.3.1 Setup Analysis Report
Path1
Slack : 0.007
Data Arrival Path:
0.5 0.4 tNET RR 1 R1C1 own_i_1/I0
Data Required Path:
Path2
Slack : 0.069
Data Arrival Path:
0.5 0.4 tNET FF 1 R1C2 own_i_2/I1
Data Required Path:
Path3
Slack : 2.0
Data Arrival Path:
0.5 0.4 tNET RR 1 R1C3 own_i_3/I2
Data Required Path:
3.3.2 Hold Analysis Report
'''
    assert setup_pin_targets(report)[0]==[]
    targets,paths=setup_pin_targets(report,relative_window_ns=.2)
    assert targets==[('RR','own_i_1','0'),('FF','own_i_2','1')]
    assert paths[0]['pins']==[['own_i_1','I0']] and len(paths)==2
    with pytest.raises(ValueError):setup_pin_targets(report,relative_window_ns=float('nan'))
