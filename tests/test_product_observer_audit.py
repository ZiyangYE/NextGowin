import copy,sys
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from audit_product_observers import audit


def fixture():
    a={'modules':{'top':{'attributes':{'top':'1'},'ports':{'d':{'direction':'input','bits':[2]},'q':{'direction':'output','bits':[3]}},
       'netnames':{'q':{'bits':[3],'attributes':{'init':'0'}}},
       'cells':{'ff':{'type':'$ff','connections':{'D':[2],'Q':[3]},'port_directions':{'D':'input','Q':'output'}}}}}}
    b=copy.deepcopy(a);m=b['modules']['top'];m['ports']['ng_state_helper_0']={'direction':'output','bits':[3]}
    m['netnames']['ng_state_helper_0']={'hide_name':0,'bits':[3],'attributes':{}}
    return a,b,[{'gold':[3],'gate':[3]}]


def test_sink_only_observer_is_exactly_nonintrusive():
    assert audit(*fixture(),'gold')['functional_cells_and_fanouts_unchanged']


@pytest.mark.parametrize('mutation',['cell','init','port','extra','undriven'])
def test_observer_audit_rejects_functional_or_undefined_changes(mutation):
    a,b,h=fixture();m=b['modules']['top']
    if mutation=='cell':m['cells']['ff']['connections']['D']=['0']
    if mutation=='init':m['netnames']['q']['attributes']['init']='1'
    if mutation=='port':del m['ports']['q']
    if mutation=='extra':m['ports']['escape']={'direction':'input','bits':[8]}
    if mutation=='undriven':
        h[0]['gold']=[9];m['ports']['ng_state_helper_0']['bits']=[9];m['netnames']['ng_state_helper_0']['bits']=[9]
    with pytest.raises(ValueError):audit(a,b,h,'gold')
