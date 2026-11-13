import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from analyze_critical_overlap import analyze


def test_shared_wire_excludes_clock_and_hold_and_preserves_bus_endpoints():
    def block(end,slack):
        return f'''Path Summary:
Slack : {slack}
From : a
To : {end}
Data Arrival Path:
 1.000 1.000 tNET RR 1 R1 a/CLK
 1.300 0.300 tC2Q RR 2 R1 a/Q
 2.300 1.000 tNET RR 1 R2 b/I0
 2.500 0.200 tINS RR 2 R2 b/F
 3.000 0.500 tNET RR 1 R3 {end}/ADB[3]
Data Required Path:
Path Statistics:
'''
    text='3.3.1 Setup Analysis Report\n'+block('c',-1)+block('d',-2)+'3.3.2 Hold Analysis Report\n'+block('c',-4)
    r=analyze(text,dict(a='launch',b='shared',c='ram0',d='ram1'))
    assert r['reported_violating_paths']==2 and r['unique_reported_endpoints']==2
    edge=r['ranked_edges'][0]
    assert (edge['source'],edge['sink'])==('launch/Q','shared/I0')
    assert edge['weighted_deficit_ns']==3 and edge['endpoint_count']==2
    assert edge['endpoints']==['ram0/ADB[3]','ram1/ADB[3]']
    assert all('CLK' not in e['sink'] for e in r['ranked_edges'])
