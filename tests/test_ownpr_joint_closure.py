import copy,json,sys
from pathlib import Path
import pytest
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from ownpr_incremental_routes import partition
from ownpr_joint_feedback import plan


class Graph:
    def node(self,r,c,name):return int(name)
    def check_pip(self,pip):return tuple(map(int,pip[2:])) in {(0,1),(1,2),(4,5),(5,6),(8,9)}


def inputs():
    nets=[dict(name='a',source=0,sinks=[2]),dict(name='b',source=4,sinks=[6]),dict(name='c',source=8,sinks=[9])]
    records={n:dict(nodes=nodes,pips=[[0,0,str(a),str(b)] for a,b in zip(nodes,nodes[1:])]) for n,nodes in [('a',[0,1,2]),('b',[4,5,6]),('c',[8,9])]}
    return nets,dict(status='routed',nets=records)


def test_incremental_releases_changed_net_and_old_tree_crossing_its_new_endpoint():
    old,routes=inputs();new=copy.deepcopy(old);new[0]['sinks']=[5]
    changed,frozen,blocked,info=partition(Graph(),old,routes,new)
    assert [n['name'] for n in changed]==['a','b'] and set(frozen)=={'c'} and blocked=={8,9}
    assert info['released_for_endpoint_or_reserved_overlap']==['b'] and info['rerouted_nets']==2
    assert frozen['c']==routes['nets']['c']
    changed,frozen,blocked,info=partition(Graph(),old,routes,new,reserved=[9])
    assert len(changed)==3 and not frozen and blocked=={9}


def test_incremental_rejects_forged_occupancy_incomplete_trees_and_shared_endpoints():
    old,routes=inputs();bad=copy.deepcopy(routes);bad['nets']['a']['nodes'].remove(1)
    with pytest.raises(ValueError,match='occupancy'):partition(Graph(),old,bad,old)
    bad=copy.deepcopy(routes);bad['nets']['a']['pips'].pop()
    with pytest.raises(ValueError,match='Disconnected'):partition(Graph(),old,bad,old)
    new=copy.deepcopy(old);new[0]['sinks']=[6]
    with pytest.raises(ValueError,match='share a physical endpoint'):partition(Graph(),old,routes,new)


def test_incremental_preserves_all_identical_trees_and_releases_removed_nets():
    old,routes=inputs();changed,frozen,blocked,info=partition(Graph(),old,routes,old)
    assert not changed and frozen==routes['nets'] and len(blocked)==8
    changed,frozen,blocked,info=partition(Graph(),old,routes,old[1:])
    assert not changed and info['removed_nets']==['a'] and 0 not in blocked


def test_real_routed_feedback_automatically_finds_critical_wide_cut_input():
    p=ROOT/'results/ownpr_uart_wide_reorder_place_001';r=ROOT/'results/ownpr_uart_wide_reorder_eco_001';s=ROOT/'results/ownpr_uart_wide_reorder_handoff_002/native'
    result=plan(p,r,s)
    top=result['candidates'][0]
    assert top['kind']=='wide_reorder' and top['root']=='ng_acd_128' and top['late_bit']==262
    assert top['witness_paths']==[1] and top['estimated_local_gain_ns']>0 and top['worst_slack_ns']==-.461
    assert len(result['negative_setup_paths'])==33 and result['negative_setup_paths'][0]['data_wire_ns']>3
    assert any(c['kind']=='wide_cut' for c in result['candidates']) and not result['timing_signoff']
    area_neutral=plan(p,r,s,max_candidates=3,max_growth=0)
    assert all(c['lut_growth']==0 and c['kind']!='wide_cut' for c in area_neutral['candidates'])


def test_output_path_feedback_automatically_selects_fixed_pad_register():
    base=ROOT/'results/ownpr_uart_selector_closure_001/round_003'
    result=plan(base/'placement',base/'route',base/'initial_export/native')
    top=result['candidates'][0]
    assert top['kind']=='output_ioreg' and top['port']=='tx_ready' and top['new_site']==[103,183,1]
    assert top['witness_paths']==[1] and top['clock_and_input_path_changes_not_estimated'] and top['lut_growth']==0


def test_ineligible_output_packing_does_not_enter_automatic_candidates(monkeypatch):
    import ownpr_ioreg_macros
    def reject(*args):raise ValueError('unsupported physical packing')
    monkeypatch.setattr(ownpr_ioreg_macros,'relocate',reject)
    base=ROOT/'results/ownpr_uart_selector_closure_001/round_003'
    result=plan(base/'placement',base/'route',base/'initial_export/native')
    assert all(r['kind']!='output_ioreg' for r in result['candidates'])
    assert any(r['key']=='output_ioreg:tx_ready' and 'unsupported' in r['reason'] for r in result['rejected'])


def test_feedback_enumerates_intermediate_and_final_selector_paths():
    from ownpr_joint_feedback import selector_costs
    from ownpr_mux_architecture import MuxArchitecture
    p=ROOT/'results/ownpr_uart_tx_ioreg_closure_002/placement'
    design=json.loads((p/'packed.json').read_text());placed=json.loads((p/'placement.json').read_text())
    costs=selector_costs(design,placed,'ng_acd_128',MuxArchitecture())
    # The bound includes both output transitions; .077625 is rise-only.
    assert set(costs)=={4,5} and costs[4]-costs[5]==pytest.approx(.091125)
    result=plan(p,ROOT/'results/ownpr_uart_tx_ioreg_closure_002/route',ROOT/'results/ownpr_uart_tx_ioreg_closure_002/initial_export')
    choices=[r for r in result['candidates'] if r['kind']=='wide_reorder' and r['root']=='ng_acd_128' and r['late_bit']==262]
    assert {r['target_index'] for r in choices}=={4,5}
    assert len({r['displaced_bit'] for r in choices})==2 and all(r['lut_growth']==0 for r in choices)


def test_feedback_rejects_changed_placement_before_candidate_generation(tmp_path):
    import shutil
    p=ROOT/'results/ownpr_uart_wide_reorder_place_001'
    shutil.copyfile(p/'packed.json',tmp_path/'packed.json');d=json.loads((p/'placement.json').read_text());d['clusters']['ng_acd_2']['slot']^=1
    (tmp_path/'placement.json').write_text(json.dumps(d))
    with pytest.raises(ValueError,match='differ from audited'):
        plan(tmp_path,ROOT/'results/ownpr_uart_wide_reorder_eco_001',ROOT/'results/ownpr_uart_wide_reorder_handoff_002/native')


def test_resume_reuses_only_completed_and_reaudited_rejections():
    from run_ownpr_joint_closure import completed_rejections
    folder=ROOT/'results/ownpr_uart_joint_closure_001';sta=ROOT/'results/ownpr_uart_wide_reorder_handoff_002/native'
    keys,records=completed_rejections(folder,json.loads((sta/'manifest.json').read_text()),json.loads((sta/'clock_tree.json').read_text()))
    assert len(keys)==len(records)==1 and records[0]['proposal']=='wide_reorder:ng_acd_128:262'
    assert records[0]['round']==1  # Interrupted round 2 is deliberately absent.


def test_resume_rejects_modified_historical_timing(tmp_path):
    from run_ownpr_joint_closure import completed_rejections
    folder=ROOT/'results/ownpr_uart_joint_closure_001';sta=ROOT/'results/ownpr_uart_wide_reorder_handoff_002/native'
    history=json.loads((folder/'manifest.json').read_text());history['rounds'][0]['evaluations'][0]['metrics']['reported_fmax_mhz']=9999
    (tmp_path/'manifest.json').write_text(json.dumps(history))
    with pytest.raises(ValueError,match='Historical rejection'):
        completed_rejections(tmp_path,json.loads((sta/'manifest.json').read_text()),json.loads((sta/'clock_tree.json').read_text()))


def test_real_automatic_joint_loop_preserves_frozen_nets_and_accepts_only_global_gain():
    from ownpr_incremental_routes import signature
    from run_ownpr_pin_closure import metric
    read=lambda p:json.loads(Path(p).read_text())
    flow=read(ROOT/'results/ownpr_uart_joint_closure_002/manifest.json')
    assert flow['status']=='bounded_rounds_complete' and flow['wall_seconds']<flow['budget_seconds']<=3600
    assert [r['status'] for r in flow['rounds']]==['rejected_global_timing','accepted','rejected_global_timing']
    assert len(flow['reused_completed_rejections'])==1
    for row in flow['rounds']:
        assert row['proof'] and all(p['cec']['equivalent'] for p in row['proof'])
        source=Path(row['source']['routes']);routed=Path(row['folder'])/'route'
        old={n['name']:n for n in read(source/'nets.json')};new={n['name']:n for n in read(routed/'nets.json')}
        frozen={n for n in old.keys()&new.keys() if signature(old[n])==signature(new[n])}
        prior=read(source/'routes.json')['nets'];after=read(routed/'routes.json')['nets'];info=row['incremental_routing']
        assert not info['fallback_full_routing'] and not info['released_for_endpoint_or_reserved_overlap']
        assert len(frozen)==info['reused_nets'] and all(prior[n]==after[n] for n in frozen)
        assert (source/'clock_tree.json').read_bytes()==(routed/'clock_tree.json').read_bytes()
    best=metric(flow['selected']['sta'])
    assert best==flow['metrics'] and best['reported_fmax_mhz']==184.437 and best['setup_violations']==31
    assert best['hold_violations']==0 and not best['native_constraints_satisfied']


def test_measured_selector_levels_have_different_global_outcomes():
    read=lambda p:json.loads(Path(p).read_text())
    flow=read(ROOT/'results/ownpr_uart_selector_closure_001/manifest.json')
    assert flow['status']=='bounded_rounds_complete' and flow['wall_seconds']<flow['budget_seconds']<=3600
    first,second,third=flow['rounds']
    assert first['proposal']['target_index']==5 and first['status']=='rejected_global_timing'
    assert second['proposal']['target_index']==4 and second['status']=='accepted'
    assert first['proposal']['late_bit']==second['proposal']['late_bit']==262
    for row in (first,second):
        before=Path(row['source']['placement']);after=Path(row['folder'])/'placement'
        assert (before/'placement.json').read_bytes()==(after/'placement.json').read_bytes()
        cells_before=read(before/'packed.json')['modules']['top']['cells'];cells_after=read(after/'packed.json')['modules']['top']['cells']
        assert {n:c['type'] for n,c in cells_before.items()}=={n:c['type'] for n,c in cells_after.items()}
        assert row['proof'] and all(p['cec']['equivalent'] for p in row['proof'])
    assert third['proposal']['kind']=='wide_cut' and third['status']=='accepted'
    assert flow['metrics']['reported_fmax_mhz']==189.977 and flow['metrics']['setup_violations']==26 and flow['metrics']['hold_violations']==0
    assert not flow['metrics']['native_constraints_satisfied']


def test_automatic_io_packing_propagates_new_clock_calibration_to_next_rewrite():
    read=lambda p:json.loads(Path(p).read_text())
    root=ROOT/'results/ownpr_uart_mixed_closure_001';flow=read(root/'manifest.json')
    assert flow['status']=='bounded_rounds_complete' and flow['wall_seconds']<flow['budget_seconds']<=3600
    first,second=flow['rounds']
    assert first['proposal']['kind']=='output_ioreg' and first['proposal']['port']=='tx_ready' and first['status']=='accepted'
    assert first['logical_netlist_byte_identical'] and first['proof']==[]
    packed=root/'round_001/ioreg/placement/packed.json'
    assert packed.read_bytes()==(Path(first['source']['placement'])/'packed.json').read_bytes()
    assert second['proposal']['kind']=='wide_cut' and second['status']=='accepted'
    capture=Path(second['source']['clock_capture'])
    assert capture==root/'round_001/ioreg/clock_encoding'
    assert (capture/'clock_tree.json').read_bytes()==(Path(second['source']['routes'])/'clock_tree.json').read_bytes()
    assert (capture/'clock_tree.json').read_bytes()!=(Path(first['source']['routes'])/'clock_tree.json').read_bytes()
    for e in second['evaluations']:
        assert Path(e['clock_capture'])==capture
        audit=read(Path(e['sta'])/'handoff_audit.json')
        assert audit['timing']['ioreg']['registers']==11 and audit['timing']['ioreg']['timing_queries']['query_vectors']==231
        assert audit['io']['ports']==24 and audit['io']['pins_preserved']
    assert flow['metrics']['reported_fmax_mhz']==192.463 and flow['metrics']['setup_violations']==25 and flow['metrics']['hold_violations']==0
    assert not flow['metrics']['native_constraints_satisfied']
