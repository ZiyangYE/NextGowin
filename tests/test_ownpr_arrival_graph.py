import copy,json,math,sys
from pathlib import Path
import pytest
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from ownpr_arrival_graph import propagate,senses,build
from prune_ownpr_lut_inputs import prune


def test_competing_input_takes_over_when_original_critical_arc_is_shortened():
    nodes=['a','b','y'];edges=[dict(source=n,destination='y',delay=[d,d],transitions=[(0,0),(1,1)]) for n,d in [('a',1.),('b',.5)]]
    seeds={'a':[3.,2.], 'b':[3.2,2.5]}
    before,_,_=propagate(nodes,edges,seeds);after,_,comp=propagate(nodes,edges,seeds,{0:[.1,.1]})
    assert before['y'][0]==4. and after['y'][0]==3.7
    assert max(comp['y'],key=lambda r:r['arrival_ns'])['source']=='b'


def test_polarity_and_cycle_guard():
    assert senses(1,1,{})=={0:[(0,1),(1,0)]} and senses(15,2,{})=={}
    edges=[dict(source='a',destination='b',delay=[.2,.3],transitions=[(0,1),(1,0)])]
    values,_,_=propagate(['a','b'],edges,{'a':[2.,1.]})
    assert values['b']==pytest.approx([1.2,2.3])
    with pytest.raises(ValueError,match='cycle'):
        propagate(['a','b'],edges+[dict(source='b',destination='a',delay=[0.,0.],transitions=[(0,0)])],{})
    with pytest.raises(ValueError,match='delay'):
        propagate(['a','b'],edges,{'a':[0.,0.]},{0:[math.nan,0.]})


def test_pruning_preserves_every_assignment_without_assuming_signal_correlation():
    cells={}
    for truth in range(16):
        cells[str(truth)]=dict(type='LUT2',parameters={'INIT':format(truth,'04b')},attributes={},
            port_directions={'I0':'input','I1':'input','F':'output'},connections={'I0':[2],'I1':[3],'F':[10+truth]})
    design={'modules':{'top':{'cells':cells}}};old=copy.deepcopy(design);after,changes=prune(design)
    ground=changes[0]['constant_bit']
    for truth in range(16):
        c=after['modules']['top']['cells'][str(truth)]
        for a in range(4):
            values={2:a&1,3:a>>1&1,ground:0}
            index=sum(values[c['connections']['I'+str(i)][0]]<<i for i in range(2))
            assert truth>>index&1==truth>>a&1
    assert design==old and prune(after)[1]==[]


def test_real_constant_leaf_has_connected_native_timing_arcs_but_no_functional_support():
    folder=ROOT/'results/ownpr_uart_arrival_graph_002'
    graph=json.loads((folder/'graph.json').read_text());meta=json.loads((folder/'manifest.json').read_text())
    arcs=[e for e in graph['edges'] if e['destination']=='own_wide_ng_acd_146_leaf_1/F' and e['kind']=='cell']
    assert len(arcs)==4 and all(not e['functional_transitions'] and len(e['transitions'])==4 for e in arcs)
    assert len(meta['reported_endpoints'])==116 and meta['maximum_absolute_difference_ns']<.006
    assert not meta['timing_signoff'] and not meta['io_envelope_independently_queried']


def test_pruned_inputs_are_static_only_with_full_truth_independence():
    from ownpr_device import Device,RoutingGraph
    from ownpr_route_design import bind
    p=ROOT/'results/ownpr_uart_ce_selector_closure_001/round_003/placement'
    design=json.loads((p/'packed.json').read_text());placed=json.loads((p/'placement.json').read_text());after,changes=prune(design)
    graph=RoutingGraph(Device(),ROOT/'results/native_rrg_001')
    nets,rows=bind(after,placed,graph)
    # Static pins require no physical tie tree: the encoded LUT ignores them.
    proven={(r['cell'],r['pin']) for r in rows if r.get('kind')=='lut_truth_independent_constant_input'}
    assert {(r['instance'],r['pin']) for r in changes} <= proven
    assert all(n['name']!='constant_0' for n in nets)


@pytest.mark.parametrize('placement,sta,count',[
    ('results/ownpr_int2float_place_004','results/native_ownpr_export_012',26),
    ('results/ownpr_uart_prune_closure_002/round_001/placement','results/ownpr_uart_prune_closure_002/round_001/initial_export/native',116)])
def test_live_graph_covers_competing_inputs_and_reported_fullio_arrivals(placement,sta,count):
    graph,labels,info=build(ROOT/placement,ROOT/sta);values,_,_=propagate(**graph)
    assert len(labels)==count and all(abs(values[n][tr]-r['reported_arrival_ns'])<.0015 for (n,tr),r in labels.items())
    assert not info['timing_signoff'] and not info['io_envelope_independently_queried']


def test_real_pruning_keeps_area_positions_and_cycles_with_cec_and_full_sta():
    p=ROOT/'results/ownpr_uart_prune_closure_002';d=json.loads((p/'manifest.json').read_text());r=d['rounds'][0]
    before=Path(d['initial']['placement']);after=Path(d['selected']['placement'])
    assert (before/'placement.json').read_bytes()==(after/'placement.json').read_bytes()
    assert all(x['cec']['equivalent'] and x['observed_hard_inputs_and_ports']==328 for x in r['proof'])
    meta=json.loads((after/'manifest.json').read_text())
    assert len(meta['unused_input_pruning'])==72
    assert d['metrics']['setup_wns_ns']>d['initial_metrics']['setup_wns_ns'] and d['metrics']['hold_violations']==0
    assert d['metrics']['reported_fmax_mhz']==195.017 and not d['metrics']['native_constraints_satisfied']


def test_wide_reordering_after_pruning_remains_exact_and_does_not_restore_dead_pins():
    from reorder_ownpr_wide_inputs import reorder
    from ownpr_mux_macros import tree
    p=ROOT/'results/ownpr_uart_prune_closure_002/round_001/placement/packed.json';before=json.loads(p.read_text())
    after,info=reorder(before,'ng_acd_146',265,4)
    def evaluate(design,values):
        cells=design['modules']['top']['cells'];drivers={bs[0]:(n,pin) for n,c in cells.items() for pin,bs in c['connections'].items() if c['port_directions'][pin]=='output'}
        known=dict(values)
        for c in cells.values():
            if c['type'] in ('GND','VCC'):known[next(iter(c['connections'].values()))[0]]=int(c['type']=='VCC')
        def get(bit):
            if bit in known:return known[bit]
            n,_=drivers[bit];c=cells[n];pins=c['connections']
            if c['type']=='LUT4':return int(c['parameters']['INIT'],2)>>sum(get(pins['I'+str(i)][0])<<i for i in range(4))&1
            return get(pins['I'+str(get(pins['S0'][0]))][0])
        return get(cells['ng_acd_146']['connections']['O'][0])
    for a in range(1<<info['width']):
        values={bit:a>>i&1 for i,bit in enumerate(info['old_order'])}
        assert evaluate(before,values)==evaluate(after,values)
    assert info['reapplied_unused_input_pruning']>0
    assert prune(after,only_cells=set(tree(after,'ng_acd_146')['leaves']))[1]==[]
