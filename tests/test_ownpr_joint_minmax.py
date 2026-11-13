import copy,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from ownpr_image_storage import load_json

def test_promising_real_tree_needs_explicit_bounded_hold_exploration():
    from ownpr_critical_tree_rebuild import hold_exploratory,exploratory
    row=load_json(ROOT/'results/vexriscv_global_tree_repair_001/searches.json')[0]['evaluations'][3]
    assert not row['eligible'] and not exploratory(row,.5)
    assert hold_exploratory(row,.55) and not hold_exploratory(row,.49)
    assert not hold_exploratory(dict(row,predicted_min_sink_setup_slack_ns=-.001),.55)

def test_hold_trace_starts_at_ff_q_after_launch_clock_path():
    from ownpr_route_eco import hold_first_wires
    text='''3.3.2 Hold Analysis Report
Path1
Slack : -0.2
Data Arrival Path:
0.0 0.0 tCL RR 1 IO pad/I
1.0 1.0 tNET RR 1 SITE ff/CLK
1.2 0.2 tC2Q RR 1 SITE ff/Q
1.3 0.1 tNET RR 1 SITE sink/CE
Data Required Path:
Path Statistics:
'''
    assert hold_first_wires(text)=={('ff/Q','sink/CE'):['sink/CE']}

def test_control_buffer_preserves_ff_data_clock_and_existing_placement():
    from ownpr_hold_buffers import insert_buffers
    from ownpr_device import Device
    p=ROOT/'results/vexriscv_global_relayout_001';d=load_json(p/'packed.json');layout=load_json(p/'placement.json');cells=d['modules']['top']['cells']
    constants={b for c in cells.values() if c['type'] in ('GND','VCC') for bs in c['connections'].values() for b in bs}
    name=next(n for n,c in cells.items() if c['type']=='DFFRE' and c['connections']['CE'][0] not in constants)
    source_bit=cells[name]['connections']['CE']
    cal=load_json(ROOT/'results/native_device_lut_004/lut_encoding_audit.json');before=copy.deepcopy(d)
    new,placed,added=insert_buffers(d,layout,Device(),cal,[dict(receiver=name,pin='CE')],2)
    assert d==before and new['modules']['top']['ports']==d['modules']['top']['ports']
    ff=new['modules']['top']['cells'][name]
    for pin in ('D','CLK','RESET','Q'):assert ff['connections'][pin]==cells[name]['connections'][pin]
    assert all(placed['clusters'][n]==v for n,v in layout['clusters'].items())
    first,second=[new['modules']['top']['cells'][r['instance']] for r in added]
    assert first['connections']['I0']==source_bit and second['connections']['I0']==first['connections']['F'] and ff['connections']['CE']==second['connections']['F']
