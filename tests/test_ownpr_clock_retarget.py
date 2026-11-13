import copy,sys
from pathlib import Path
import pytest
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from ownpr_clock_graph import ClockGraph
from ownpr_image_storage import load_json
from ownpr_device import Device


def test_retarget_keeps_old_sink_paths_and_removes_unused_endpoint():
    old=load_json(ROOT/'results/tmds_fast_ff_inputs_001/route/clock_tree.json');graph=ClockGraph.for_tree(old);d=Device()
    old_sink=[101,163,d.ids[d.register_bindings_for('DFFRE')[2]['CLK']]]
    new_sink=[98,166,d.ids[d.register_bindings_for('DFFRE')[0]['CLK']]]
    before={tuple(p[0]):p for p in old['paths_sink_to_source']};assert tuple(old_sink) in before
    sinks=[list(s) for s in before if s!=tuple(old_sink)]+[new_sink]
    new=graph.retarget(old,old['source'],sinks);after={tuple(p[0]):p for p in new['paths_sink_to_source']}
    assert new['retained_sink_paths']==37 and new['added_sink_paths']==new['removed_sink_paths']==1
    assert set(after)==set(map(tuple,sinks))
    assert all(after[s]==p for s,p in before.items() if s!=tuple(old_sink))
    assert graph.audit_paths(new['paths_sink_to_source'])['status']=='clock_paths_match'
    same=graph.retarget(old,old['source'],[list(s) for s in before])
    assert {tuple(p[0]):p for p in same['paths_sink_to_source']}==before


def test_retarget_rejects_changed_source_or_inconsistent_parents():
    old=load_json(ROOT/'results/tmds_fast_ff_inputs_001/route/clock_tree.json');graph=ClockGraph.for_tree(old);sinks=[p[0] for p in old['paths_sink_to_source']]
    with pytest.raises(ValueError,match='source'):graph.retarget(old,[0,0,0],sinks)
    broken=copy.deepcopy(old);broken['parents'][0]['parent']=broken['parents'][0]['node']
    with pytest.raises(ValueError,match='paths/parents'):graph.retarget(broken,old['source'],sinks)
