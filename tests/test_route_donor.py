import json,sys
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import prepare_route_donor as donor

def fixture(tmp_path):
    base=tmp_path/'base';reference=tmp_path/'reference';base.mkdir();reference.mkdir()
    nets=[dict(name='critical',source=1,sinks=[3],clock=False),dict(name='blocker',source=8,sinks=[9],clock=False)]
    current=dict(status='routed',nets={'critical':dict(nodes=[1,4,3],pips=[]),'blocker':dict(nodes=[8,2,9],pips=[])})
    previous=dict(status='routed',nets={'critical':dict(nodes=[1,2,3],pips=[]),'blocker':dict(nodes=[8,5,9],pips=[])})
    for folder,routes in ((base,current),(reference,previous)):
        for name,data in [('manifest.json',dict(status='routed',vendor_pnr_executed=False)),('nets.json',nets),('all_nets.json',nets),('clock_tree.json',{}),('routes.json',routes)]:
            (folder/name).write_text(json.dumps(data))
    return base,reference

def test_only_blocking_network_is_released(tmp_path,monkeypatch):
    monkeypatch.setattr(donor,'reserve',lambda *args:None);base,reference=fixture(tmp_path)
    result=donor.prepare(base,reference,'critical',tmp_path/'out',1)
    assert result['blockers']==['blocker'] and result['independent_admission_required']
    record=json.loads((tmp_path/'out/partial_routes.json').read_text())
    assert set(record['nets'])=={'critical'} and not record['connectivity_verified']

def test_endpoint_collision_and_closure_limit_are_rejected(tmp_path,monkeypatch):
    monkeypatch.setattr(donor,'reserve',lambda *args:None);base,reference=fixture(tmp_path)
    with pytest.raises(ValueError,match='bound'):donor.prepare(base,reference,'critical',tmp_path/'out',0)
    nets=json.loads((base/'nets.json').read_text());nets[1]['sinks']=[2];(base/'nets.json').write_text(json.dumps(nets))
    with pytest.raises(ValueError,match='endpoint'):donor.prepare(base,reference,'critical',tmp_path/'out',1)
    assert not (tmp_path/'out').exists()
