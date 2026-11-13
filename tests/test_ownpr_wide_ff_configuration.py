import copy,sys
from pathlib import Path
import pytest
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
import ownpr_wide_ff_configuration as model
from ownpr_device import Device,RoutingGraph
from ownpr_router import route,validate

CAPTURE=ROOT/'results/native_wide_ff_encoding_003'
PROFILE=dict(cell_type='DFFRE',ce='one',control='signal',clock='signal')


@pytest.fixture(scope='module')
def calibrated_graph():
    return model.SelectedWideFfGraph(Device(),ROOT/'results/native_rrg_001',CAPTURE,[(49,89,5,0),(100,163,7,3)])


def test_complete_wide_ff_encoding_and_isolated_timing(calibrated_graph):
    a=calibrated_graph.calibration
    assert a['status']=='wide_ff_configuration_and_isolated_wire_queries_matched'
    assert len(a['records'])==6 and not a['production_packer_enabled'] and not a['full_sta_verified']
    assert all(r['changed_frame_bits']==1 and r['ff_profile']==PROFILE for r in a['records'])
    assert all(r['native_wire_delay_by_selector']=={str(s):[0.]*4 for s in range(3)} for r in a['records'])
    assert all(len(r['configuration_requirements'])==1 and r['configuration_requirements'][0]['value']==1 for r in a['records'])


def test_only_nominated_wide_ff_terminals_are_routable(calibrated_graph):
    g=calibrated_graph
    for pip,(source,sink) in g.selected_links.items():
        nets=[dict(name='wide_ff',source=source,sinks=[sink],criticality=1.)]
        result=route(g,nets,timeout=10,max_expansions=100)
        checked=validate(g,nets,result)
        assert checked['connectivity_verified'] and checked['no_node_conflicts']
        assert result['nets']['wide_ff']['pips']==[pip]
        assert result['nets']['wide_ff']['parent'][sink]==source
        assert g.check_pip(pip)
    assert not g.check_pip((49,89,'OF1','DI1'))
    assert not g.check_pip((49,90,'OF0','DI0'))
    ordinary=RoutingGraph(g.device,ROOT/'results/native_rrg_001')
    assert not ordinary.check_pip((49,89,'OF0','DI0'))
    assert not ordinary.uphill(ordinary.node(49,89,'DI0'))


@pytest.mark.parametrize('level,slot',[(5,0),(6,1),(7,3)])
def test_wide_leaf_activation_merges_with_ff_control_group(level,slot):
    attrs=model.groups(level,slot,PROFILE)
    assert [a[0] for a in attrs]==[9]*(1<<(level-5))+[0]*(4-(1<<(level-5)))
    assert attrs[slot//2][1]==12 and attrs[slot//2][9:11]==[2,3]
    assert all(a[1]==0 for i,a in enumerate(attrs) if i!=slot//2)
    with pytest.raises(ValueError,match='root slot'):model.groups(level,slot+1,PROFILE)
    with pytest.raises(ValueError,match='control profile'):model.groups(level,slot,dict(PROFILE,ce='signal'))


def test_incorrect_capture_mode_is_rejected_before_accepting_bits(monkeypatch):
    original=model.load_json
    def changed(path):
        data=original(path)
        if Path(path).name=='encoding.json':
            data=copy.deepcopy(data);data['records'][0]['ff_profile']['ce']='signal'
        return data
    monkeypatch.setattr(model,'load_json',changed)
    with pytest.raises(ValueError,match='control profile'):model.audit_capture(CAPTURE)


def test_conflicting_sites_and_uncalibrated_shapes_rejected(calibrated_graph,monkeypatch):
    # Reuse already independently verified calibration; avoid rereading images
    # for geometry rejection tests, whose input is the selected site list.
    monkeypatch.setattr(model,'audit_capture',lambda _:calibrated_graph.calibration)
    for selections in ([(49,89,5,0),(49,89,6,1)],[(49,89,8,7)],[(-1,89,5,0)],[(49,89,5,2)]):
        with pytest.raises(ValueError):model.SelectedWideFfGraph(calibrated_graph.device,ROOT/'results/native_rrg_001',CAPTURE,selections)
