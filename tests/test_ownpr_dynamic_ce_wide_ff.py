import copy,sys
from pathlib import Path
import pytest
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from ownpr_device import Device,RoutingGraph
from ownpr_image_storage import load_json
from ownpr_wide_ff_packing import calibration,pairs
from ownpr_timing_wide_remap import transform,place
from ownpr_wide_ff_configuration import groups


@pytest.fixture(scope='module')
def calibrated():return calibration(True)


def test_dynamic_ce_combined_calibration_checks_all_six_cases(calibrated):
    assert len(calibrated['records'])==6 and not calibrated['full_sta_verified']
    for r in calibrated['records']:
        assert r['ff_profile']==dict(cell_type='DFFRE',ce='signal',control='zero',clock='signal')
        assert r['changed_frame_bits']==1 and len(r['configuration_requirements'])==1
        a=groups(r['level'],r['slot'],r['ff_profile'])[r['slot']//2]
        assert a[5:7]==[0,3] and a[9:11]==[1,0]


@pytest.fixture(scope='module')
def cpu_pair(calibrated):
    base=ROOT/'results/vexriscv_batch_cut_place_002';d=load_json(base/'packed.json');p=load_json(base/'placement.json')
    new,change=transform(d,'ng_coopt_3080','ng_coopt_3239',8370)
    result,physical=place(new,p,'ng_coopt_3239',Device(),allow_paired=True)
    return d,p,new,result,change,physical


def test_cpu_pair_requires_explicit_clock_replan_and_preserves_ff_logic(cpu_pair):
    from test_ownpr_register_move import clock_endpoints
    d,p,new,np,change,physical=cpu_pair;device=Device();ff=p['clusters']['ng_coopt_3239']['ff']
    assert new['modules']['top']['cells'][ff]==d['modules']['top']['cells'][ff]
    assert physical['moved_ff']==ff and physical['clock_replan_required']
    assert all(np[k]==p[k] for k in ('fixed_io','hard_clusters','arithmetic_clusters'))
    oldpoints,newpoints=clock_endpoints(p,device),clock_endpoints(np,device)
    assert len(oldpoints-newpoints)==len(newpoints-oldpoints)==1
    row=next(r for r in pairs(new,np,device) if r['ff']==ff)
    assert row['level']==6 and row['profile']['ce']=='signal' and row['profile']['control']=='zero'
    assert row['calibration_sha256']==calibration(True)['capture_sha256']
    with pytest.raises(ValueError,match='state pair'):place(new,p,'ng_coopt_3239',device)


def test_uncalibrated_dynamic_ce_plus_dynamic_reset_is_rejected(cpu_pair):
    _,_,d,p,_,physical=cpu_pair;d=copy.deepcopy(d);ff=d['modules']['top']['cells'][physical['moved_ff']]
    ff['connections']['RESET']=ff['connections']['CE'][:]
    with pytest.raises(ValueError,match='combined calibration'):pairs(d,p,Device())


def test_dynamic_pair_exposes_only_its_selected_data_edge(cpu_pair):
    from ownpr_route_design import bind
    from ownpr_wide_ff_packing import configure_graph
    _,_,d,p,_,physical=cpu_pair;g=RoutingGraph(Device(),ROOT/'results/native_rrg_001');rows=configure_graph(g,d,p)
    row=next(r for r in rows if r['ff']==physical['moved_ff']);r,c,s=row['row'],row['column'],row['slot']
    assert g.check_pip((r,c,'OF'+str(s),'DI'+str(s)))
    assert not g.check_pip((r,c,'OF0','DI0'))


def test_complete_cpu_handoff_keeps_independent_ce_and_mux_selector_routes():
    from audit_ownpr_wide_ff import audit
    folder=ROOT/'results/vexriscv_dynamic_wide_trial_001/export/native';placement=ROOT/'results/vexriscv_dynamic_wide_place_001'
    result=audit(folder,placement);row=result['records'][0]
    assert row['level']==6 and row['configuration_calibration_sha256']==calibration(True)['capture_sha256']
    d=load_json(placement/'packed.json')['modules']['top']['cells'];ff=row['ff'];root=row['root']
    assert d[ff]['connections']['CE']!=d[root]['connections']['S0']
    captures=load_json(folder/'sta_endpoint_audit.json')
    ce=next(r for r in captures if r['instance']==ff and r['pin']=='CE')
    selector=next(r for r in captures if r['instance']==root and r['pin']=='S0')
    assert len(ce['nodes'])==len(selector['nodes'])==1 and ce['nodes'][0]['node']!=selector['nodes'][0]['node']
    tree=load_json(ROOT/'results/vexriscv_dynamic_wide_trial_001/clock_tree.json')
    assert tree['retained_sink_paths']==1545 and len(tree['paths_sink_to_source'])==1546
