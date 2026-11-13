import copy,json,sys
from pathlib import Path
import pytest
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from ownpr_reorder_prediction import project,projected_metrics,rank,assess_routed
from ownpr_arrival_graph import build,propagate
from ownpr_cell_timing import NativeCellTiming
from reorder_ownpr_wide_inputs import reorder


@pytest.fixture(scope='module')
def context():
    p=ROOT/'results/ownpr_uart_prune_closure_002/round_001';place=p/'placement';sta=p/'initial_export/native'
    graph,labels,info=build(place,sta);design=json.loads((place/'packed.json').read_text());placed=json.loads((place/'placement.json').read_text())
    return graph,labels,design,placed,NativeCellTiming()


def test_identity_rebuild_preserves_every_arrival_without_transferring_wires(context):
    graph,labels,design,placed,library=context
    projected,transfers=project(graph,design,design,placed,'ng_acd_146',library)
    before,_,_=propagate(**graph);after,_,_=propagate(**projected)
    assert not transfers and before.keys()==after.keys()
    for node in before:assert before[node]==pytest.approx(after[node])
    assert projected_metrics(labels,before,after)['setup_wns_ns']==-.128


def test_rewrite_transfers_only_internal_inputs_and_keeps_external_graph_frozen(context):
    graph,labels,design,placed,library=context;after,_=reorder(design,'ng_acd_146',265,4)
    from ownpr_mux_macros import tree
    spec=tree(design,'ng_acd_146');members=set(spec['leaves'])|set(spec['muxes'])
    high,transfers=project(graph,design,after,placed,'ng_acd_146',library)
    low,_=project(graph,design,after,placed,'ng_acd_146',library,'min_observed')
    assert transfers and high['seeds']==graph['seeds']
    outside=lambda e:e['destination'].rsplit('/',1)[0] not in members
    assert [e for e in high['edges'] if outside(e)]==[e for e in graph['edges'] if outside(e)]
    high_values,_,_=propagate(**high);low_values,_,_=propagate(**low)
    assert all(h>=l for n in high_values for h,l in zip(high_values[n],low_values[n]))


def test_projection_rejects_unrelated_logic_or_state_change(context):
    graph,labels,design,placed,library=context;changed=copy.deepcopy(design)
    changed['modules']['top']['cells']['ng_acd_2']['parameters']['INIT']='0000000000000000'
    with pytest.raises(ValueError,match='another macro'):project(graph,design,changed,placed,'ng_acd_146',library)


def test_rank_uses_competing_endpoints_instead_of_largest_local_arc_gain():
    from ownpr_joint_feedback import plan
    p=ROOT/'results/ownpr_uart_prune_closure_002/round_001';feedback=plan(p/'placement',p/'route',p/'initial_export/native',max_candidates=200,max_growth=0)
    assert feedback['candidates'][0]['root']=='ng_acd_147'
    ranked,info=rank(p/'placement',p/'initial_export/native',feedback['candidates'])
    assert ranked[0]['key']=='wide_reorder:ng_acd_146:265:select5'
    assert ranked[0]['arrival_projection']['max_observed']['metrics']['setup_violations']==14
    assert info['native_arrival_max_error_ns']<.001 and not info['timing_signoff']


def test_measured_shared_branches_reveal_regression_outside_rewritten_macro():
    from run_ownpr_pin_closure import metric
    base=ROOT/'results/ownpr_uart_prune_closure_002/round_001';candidate=ROOT/'results/ownpr_uart_arrival_reorder_closure_001/round_001'
    r=assess_routed(base/'placement',base/'initial_export/native',candidate/'placement',candidate/'route',candidate/'initial_export/wire','ng_acd_146')
    actual=metric(candidate/'initial_export/native')
    assert abs(r['metrics']['setup_wns_ns']-actual['setup_wns_ns'])<.001
    assert r['metrics']['setup_violations']==actual['setup_violations']==14
    assert r['metrics']['setup_wns_ns']<-.24 and not r['timing_signoff']
    outside=r['wire_measurements']['changed_outside_macro']
    branch=next(x for x in outside if x['endpoint']=='own_wide_ng_acd_147_mux_6_1/S0')
    assert branch['after'][0]-branch['before'][0]==pytest.approx(.153,abs=1e-6)
    assert any(p['endpoint']=='own_wide_ng_acd_146_leaf_0/I2' for p in r['wire_measurements']['reactivated_macro_pins'])


def test_pipeline_prediction_precedes_export_and_signed_zero_reexport_is_separate():
    original=ROOT/'results/ownpr_uart_arrival_reorder_closure_002';flow=json.loads((original/'manifest.json').read_text());entry=flow['rounds'][0]
    assert entry['status']=='failed'  # The original failed audit was not rewritten.
    stages=[r['stage'] for r in entry['stages']]
    assert stages.index('initial_export_wire')<stages.index('initial_export')
    assert stages.index('eco_export_wire')<stages.index('eco_export')
    assert len(entry['routed_arrival_predictions'])==2
    fresh=json.loads((ROOT/'results/ownpr_uart_arrival_reexport_001/manifest.json').read_text())
    assert fresh['status']=='verified_handoff_complete' and fresh['metrics']['setup_violations']==21
    predicted=entry['routed_arrival_predictions'][-1]['metrics']
    assert abs(predicted['setup_wns_ns']-fresh['metrics']['setup_wns_ns'])<.001
    assert fresh['metrics']['hold_violations']==0 and not fresh['native_constraints_satisfied']
