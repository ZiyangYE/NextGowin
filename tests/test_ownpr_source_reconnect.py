import sys,time,copy
from pathlib import Path
import pytest
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from ownpr_source_reconnect import fragments,reconnect_source
from ownpr_router import validate
from test_ownpr_branch_reuse import Graph,source

def test_moved_source_preserves_shared_downstream_branches():
    g=Graph();old,routes=source();record=routes['nets']['a'];new=dict(old,source=7)
    result,info=reconnect_source(g,old,record,new,{},time.monotonic()+5,max_depth=1)
    assert len(result)==1 and result[0]['retained_pips']==2
    row=result[0]['record'];assert 0 not in row['nodes']
    assert {(0,0,'1','2'),(0,0,'1','3')}<=set(map(tuple,row['pips']))
    assert validate(g,[new],dict(nets={'a':row}))['connectivity_verified']

def test_new_sink_is_connected_and_foreign_resources_remain_reserved():
    g=Graph();old,routes=source();new=dict(old,source=7,sinks=[2,3,8])
    result,_=reconnect_source(g,old,routes['nets']['a'],new,{},time.monotonic()+5,max_depth=1)
    assert result and 8 in result[0]['record']['nodes']
    result,info=reconnect_source(g,old,routes['nets']['a'],new,{1:'other'},time.monotonic()+5,max_depth=1)
    assert not result and info['trials'][0]['status']=='fragment_occupied'

def test_fragment_anchor_inherits_the_most_critical_downstream_receiver():
    g=Graph();events=[];g.begin_net=lambda net:events.append(net)
    old,routes=source();new=dict(old,source=7,sink_criticalities={2:.2,3:.9})
    result,_=reconnect_source(g,old,routes['nets']['a'],new,{},time.monotonic()+5,max_depth=1)
    assert result and events[0]['sink_criticalities']=={1:.9}
    with pytest.raises(ValueError):fragments(g,old,routes['nets']['a'],dict(new,sources=[9]),1)

def test_source_cut_rejects_corruption_constants_and_ambiguous_parents():
    g=Graph();old,routes=source();new=dict(old,source=7);record=copy.deepcopy(routes['nets']['a']);record['nodes'].append(9)
    with pytest.raises(ValueError):fragments(g,old,record,new,1)
    with pytest.raises(ValueError):fragments(g,dict(old,constant_value=0),routes['nets']['a'],new,1)
    record=copy.deepcopy(routes['nets']['a']);record['pips'].append([0,0,'4','3']);record['pips'].append([0,0,'0','4']);record['nodes'].append(4)
    with pytest.raises(ValueError,match='multiple parents'):fragments(g,old,record,new,1)


def test_exact_cut_cannot_fall_back_or_take_a_foreign_receiver():
    g=Graph();old,routes=source();before=copy.deepcopy(routes);new=dict(old,source=7)
    result,info=reconnect_source(g,old,routes['nets']['a'],new,{2:'retained_donor'},time.monotonic()+5,min_depth=2,max_depth=2)
    assert not result and info['trials']==[dict(depth=2,status='fragment_occupied',overlap_nodes=1)]
    assert routes==before
    with pytest.raises(ValueError,match='depth range'):reconnect_source(g,old,routes['nets']['a'],new,{},time.monotonic()+5,min_depth=3,max_depth=2)
