import json,sys
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from ownpr_image_storage import sha
from ownpr_source_configuration import stage,load


def fixture(tmp_path):
    source=tmp_path/'route';source.mkdir();placement=source/'placement';placement.mkdir();out=tmp_path/'out';out.mkdir()
    for name in ('packed.json','placement.json'):(placement/name).write_text('{}')
    meta=dict(status='routed',vendor_pnr_executed=False,netlist_sha256=sha(placement/'packed.json'),placement_sha256=sha(placement/'placement.json'))
    (source/'nets.json').write_text('[]');(source/'routes.json').write_text('{"status":"routed","nets":{}}')
    (source/'manifest.json').write_text(json.dumps(meta));return source,placement,out


def test_exact_source_is_snapshotted_and_mutations_are_rejected(tmp_path):
    source,placement,out=fixture(tmp_path);record=stage(source,placement,out)
    assert record['source_placement']==str(placement.resolve()) and load(out,source)[:2]==({}, {})
    (out/'reuse_source/packed.json').write_text('{"forged":1}')
    with pytest.raises(ValueError,match='snapshot'):load(out,source)


def test_explicit_wrong_source_does_not_fall_back_to_a_matching_target(tmp_path):
    source,placement,out=fixture(tmp_path);wrong=tmp_path/'wrong';wrong.mkdir()
    for name in ('packed.json','placement.json'):(wrong/name).write_text('{"different":true}')
    with pytest.raises(ValueError,match='unavailable'):stage(source,placement,out,wrong)


def test_source_manifest_change_is_rejected_after_staging(tmp_path):
    source,placement,out=fixture(tmp_path);stage(source,placement,out)
    (source/'manifest.json').write_text((source/'manifest.json').read_text()+'\n')
    with pytest.raises(ValueError,match='provenance'):load(out,source)


def test_route_input_change_after_source_staging_is_rejected(tmp_path):
    source,placement,out=fixture(tmp_path);stage(source,placement,out)
    (source/'nets.json').write_text('[{"forged":1}]')
    with pytest.raises(ValueError,match='route inputs'):load(out,source)


def test_new_target_terminal_cannot_authorize_a_forged_old_route():
    from ownpr_device import ROOT,Device,RoutingGraph
    from ownpr_incremental_routes import source_validation_graph,partition
    from ownpr_router import validate
    from test_general_wide_recompile import grounded
    d,l,_,_=grounded(6);device=Device();r,c=next((r,c) for r,row in enumerate(device.grid) for c,t in enumerate(row) if t==17)
    graph=RoutingGraph(device,ROOT/'results/native_rrg_001');pip=(r,c,'OF0','DI0');graph.select_terminal_pips([pip])
    net=dict(name='forged_old',source=graph.node(r,c,'OF0'),sinks=[graph.node(r,c,'DI0')])
    routes=dict(status='routed',nets={'forged_old':dict(nodes=sorted({net['source'],*net['sinks']}),pips=[pip])})
    assert validate(graph,[net],routes)['connectivity_verified']
    oldgraph=source_validation_graph(graph,routes,ROOT/'results/native_rrg_001',design=d,placement=l)
    assert not oldgraph.check_pip(pip) and graph.check_pip(pip)
    with pytest.raises(ValueError,match='Nonexistent'):partition(graph,[net],routes,[net],source_graph=oldgraph)
