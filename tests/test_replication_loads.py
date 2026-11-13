import copy,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from audit_replication_loads import compare

def test_replica_adds_control_load_even_when_old_data_fanout_is_reduced():
    cells={'pad':dict(type='IBUF',port_directions={'O':'output'},connections={'O':[1]}),
           'ff':dict(type='DFFRE',port_directions={'RESET':'input','Q':'output'},connections={'RESET':[1],'Q':[2]}),
           'lut':dict(type='LUT1',port_directions={'I0':'input'},connections={'I0':[2]})}
    before={'modules':{'top':{'cells':cells}}};after=copy.deepcopy(before);c=after['modules']['top']['cells']
    c['replica']=copy.deepcopy(c['ff']);c['replica']['connections']['Q']=[3];c['lut']['connections']['I0']=[3]
    result=compare(before,after);rows={r['bit']:r for r in result['unchanged_driver_load_changes']}
    assert result['cell_delta']=={'DFFRE':1}
    assert rows[1]['control_loads_after']==2 and rows[1]['control_loads_before']==1
    assert rows[2]['total_loads_before']==1 and rows[2]['total_loads_after']==0
    assert result['changed_or_new_driver_bits']==[3]
