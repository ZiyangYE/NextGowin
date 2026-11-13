import copy,json,sys
from collections import defaultdict
from pathlib import Path
import pytest
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from run_ownpr_clock_closure import clock_identity
from ownpr_clock_coverage import native_paths


class Graph:
    def __init__(self,edges):
        self.nodes=[dict(coordinate=[0,0,i]) for i in range(max(max(e) for e in edges)+1)]
        self.by_coordinate={tuple(r['coordinate']):i for i,r in enumerate(self.nodes)}
        self.adj=defaultdict(list)
        for a,b in edges:self.adj[a].append(b)


def test_discovery_enumerates_cycles_as_simple_paths_without_fabricating_loops():
    g=Graph([(0,1),(1,2),(2,1),(1,3),(2,3)])
    paths,info=native_paths(g,[0,0,0],[[0,0,3]])
    assert set(paths[3])=={(0,1,3),(0,1,2,3)} and info['enumeration_complete'] and info['cyclic_graph']
    paths,info=native_paths(g,[0,0,0],[[0,0,3]],max_paths=1)
    assert len(paths[3])==1 and not info['enumeration_complete']
    paths,info=native_paths(g,[0,0,0],[[0,0,3]],max_expansions=1)
    assert not info['enumeration_complete'] and not paths[3]


def test_discovery_preserves_blocked_resources_and_enumeration_budget():
    g=Graph([(0,1),(0,2),(1,3),(2,3)])
    paths,info=native_paths(g,[0,0,0],[[0,0,3]],blocked=[[0,0,1]])
    assert paths[3]==[(0,2,3)] and info['enumeration_complete']
    with pytest.raises(ValueError,match='overlaps'):native_paths(g,[0,0,0],[[0,0,3]],blocked=[[0,0,3]])
    with pytest.raises(ValueError,match='budget'):native_paths(g,[0,0,0],[[0,0,3]],max_paths=1)


def test_cyclic_discovery_allocates_budget_to_each_sink():
    g=Graph([(0,1),(1,2),(2,1),(1,3),(2,3),(0,4)])
    paths,info=native_paths(g,[0,0,0],[[0,0,3],[0,0,4]],max_paths=2,max_expansions=100)
    assert len(paths[3])==len(paths[4])==1 and not info['enumeration_complete']
    assert paths[4]==[(0,4)] and len(info['per_sink'])==2


def test_clock_identity_checks_parent_paths_and_source_without_metadata_order():
    tree=dict(source=[0,0,0],parents=[dict(node=[0,0,0],parent=None),dict(node=[0,0,1],parent=[0,0,0])],paths_sink_to_source=[[[0,0,1],[0,0,0]]])
    other=copy.deepcopy(tree);other['parents'].reverse();other['scope']='another serialization'
    assert clock_identity(tree)==clock_identity(other)
    other['source']=[0,0,2];assert clock_identity(tree)!=clock_identity(other)
    other=copy.deepcopy(tree);other['parents'].append(other['parents'][0])
    with pytest.raises(ValueError,match='Duplicate'):clock_identity(other)


def test_loaded_clock_changes_require_full_vectors_and_fixed_endpoints():
    from ownpr_clock_timing_search import merged_load_changes
    before=[dict(node=[1,2,3],delays_by_selector=[[.1]*4,[.2]*4,[.05]*4])]
    after=copy.deepcopy(before);after[0]['delays_by_selector'][1][2]=.3
    delta=merged_load_changes(before,after)
    assert len(delta)==1 and delta[0]['slow_rise_change_ns']==pytest.approx(.1)
    assert not merged_load_changes(before,before)
    bad=copy.deepcopy(after);bad[0]['node']=[2,2,3]
    with pytest.raises(ValueError,match='endpoints'):merged_load_changes(before,bad)
    bad=copy.deepcopy(after);bad[0]['delays_by_selector'][1][2]=float('nan')
    with pytest.raises(ValueError,match='Invalid'):merged_load_changes(before,bad)
    with pytest.raises(ValueError,match='duplicate'):merged_load_changes(before,after+after)


def test_current_clock_closure_keeps_data_and_checks_all_final_metrics():
    p=ROOT/'results/ownpr_uart_clock_closure_001';d=json.loads((p/'manifest.json').read_text())
    assert d['status']=='bounded_clock_attempt_complete' and d['clock_paths_unchanged'] and d['wall_seconds']<600
    assert d['evaluations'][0]['decision']=='unchanged_verified'
    observed=d['evaluations'][0]['metrics'];before=d['initial_metrics']
    assert {k:v for k,v in observed.items() if k!='handoff_sha256'}=={k:v for k,v in before.items() if k!='handoff_sha256'}
    for name in ('routes.json','nets.json','all_nets.json','static_connections.json'):
        assert (p/'route'/name).read_bytes()==(Path(d['initial']['routes'])/name).read_bytes()
    assert d['selected']==d['initial'] and not d['timing_signoff']


@pytest.mark.parametrize('folder,required,changed',[
    ('native_clock_spclk33_prefix_encoding_001',2,1),
    ('native_clock_spclk33_encoding_001',18,9)])
def test_spclk33_source_mask_matches_isolated_and_complete_native_captures(folder,required,changed):
    from audit_native_clock_encoding import audit
    p=ROOT/'results'/folder;r=audit(p,p/'clock_tree.json')
    assert r['status']=='clock_candidate_bits_matched' and r['required_bits']==required and r['actual_changes']==changed
    assert r['full_payload_restored'] and r['unexpected']==r['missing']==0
    assert r['edge_kinds']['spclk33_source_mux']==1


def test_clock_latency_search_cannot_accept_global_tns_regression():
    p=ROOT/'results/ownpr_uart_clock_closure_003';d=json.loads((p/'manifest.json').read_text())
    assert d['status']=='bounded_clock_attempt_complete' and not d['clock_paths_unchanged']
    e=d['evaluations'][0];assert e['decision']=='rejected_global_timing' and d['selected']==d['initial']
    assert e['metrics']['total_negative_setup_slack_ns']<d['initial_metrics']['total_negative_setup_slack_ns']
    r=json.loads((p/'search/search_report.json').read_text())
    assert r['merged_load_remeasured'] and not r['merged_delay_predictions_exact'] and len(r['merged_load_changes'])==73
    audit=json.loads((p/'export/native/handoff_audit.json').read_text())
    assert audit['configuration_bits_verified']>23000 and not audit['timing_signoff']
