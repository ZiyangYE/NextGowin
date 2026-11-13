import copy,json,sys
from pathlib import Path
import pytest
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from ownpr_lut_lifting import lift,independent
from ownpr_lut_pin_eco import permute
from ownpr_pin_slack import downstream_budgets
from ownpr_device import Device,RoutingGraph
from ownpr_route_design import bind
from audit_ownpr_native_export import audit
from run_ownpr_pin_closure import improves


def read(p):return json.loads(p.read_text())


def test_all_small_lut_functions_survive_lifting_and_fast_pin_permutation():
    for width in (1,2,3):
        for truth in range(1<<(1<<width)):
            cell=dict(type='LUT'+str(width),parameters={'INIT':format(truth,f'0{1<<width}b')},connections={**{f'I{i}':[i] for i in range(width)},'F':[100]},port_directions={**{f'I{i}':'input' for i in range(width)},'F':'output'})
            extended=lift(cell,999)
            for pin in range(width,4):assert independent(extended,pin)
            for pin in range(width):
                changed=permute(extended,pin,3)
                for stimulus in range(1<<width):
                    values={i:(stimulus>>i)&1 for i in range(width)};values[999]=0
                    address=sum(values[changed['connections']['I'+str(i)][0]]<<i for i in range(4))
                    assert ((int(changed['parameters']['INIT'],2)>>address)&1)==((truth>>stimulus)&1)


def test_only_truth_independent_constant_lut_inputs_skip_routing():
    folder=ROOT/'results/ownpr_uart_place_003';design=read(folder/'packed.json');placed=read(folder/'placement.json');cells=design['modules']['top']['cells']
    ground=next(c['connections']['G'][0] for c in cells.values() if c['type']=='GND');cells['ng_acd_4']=permute(lift(cells['ng_acd_4'],ground),0,3)
    graph=RoutingGraph(Device(),ROOT/'results/native_rrg_001');nets,static=bind(design,placed,graph)
    assert len(nets)==265 and not any(n['name'].startswith('constant_') for n in nets)
    assert any(r.get('kind')=='lut_truth_independent_constant_input' and r['cell']=='ng_acd_4' and r['pin']=='I0' for r in static)
    cells['ng_acd_4']['parameters']['INIT']='1010101010101010'
    nets,static=bind(design,placed,graph)
    assert any(n['name']=='constant_0' for n in nets)
    assert not any(r.get('kind')=='lut_truth_independent_constant_input' and r['cell']=='ng_acd_4' and r['pin']=='I0' for r in static)


def test_hold_budget_tracks_specific_downstream_control_pins():
    folder=ROOT/'results/native_ownpr_uart_export_011';design=read(ROOT/'results/ownpr_uart_place_003/packed.json');mapping=read(folder/'name_map.json')
    budget=downstream_budgets(design,mapping,(folder/'probe.tr').read_text())
    assert budget('ng_acd_4','I0')>.037
    with pytest.raises(ValueError,match='clock cone'):
        ff=next(n for n,c in design['modules']['top']['cells'].items() if c['type']=='DFFCE');budget(ff,'CLK')


def test_lifted_real_uart_cell_keeps_site_and_passes_native_readback():
    source=ROOT/'results/ownpr_uart_place_003';eco=ROOT/'results/ownpr_uart_pin_eco_001';meta=read(eco/'manifest.json')
    assert meta['cec'] and all(p['cec']['equivalent'] for p in meta['cec'])
    assert (source/'placement.json').read_bytes()==(eco/'placement/placement.json').read_bytes()
    before=read(source/'packed.json')['modules']['top']['cells'];after=read(eco/'placement/packed.json')['modules']['top']['cells']
    assert len(before)==len(after) and before['ng_acd_113']['type']=='LUT3' and after['ng_acd_113']['type']=='LUT4'
    assert independent(after['ng_acd_113'],0)
    r=audit(ROOT/'results/native_ownpr_uart_export_012',eco/'placement')
    assert r['configuration_bits_verified']==20142 and r['connectivity']['lut_functions']==137 and r['io']['ports']==24
    assert r['timing']['hold_violated_endpoints']==0 and not r['native_sta_constraints_satisfied']


def test_whole_design_rejection_keeps_last_verified_candidate():
    flow=read(ROOT/'results/ownpr_uart_pin_closure_001/manifest.json');rejected=flow['rounds'][2]
    assert rejected['status']=='rejected_global_timing'
    assert rejected['pin_permutation']['gain_ns']>0.4
    assert not improves(rejected['before'],rejected['after'])
    assert flow['metrics']['setup_wns_ns']==-1.02 and 'round_002' in flow['selected']['sta']
    bad=copy.deepcopy(flow['metrics']);bad['setup_wns_ns']=-.5;bad['hold_violations']=1
    assert not improves(flow['metrics'],bad)


def test_cached_clock_model_keeps_partition_validation():
    from ownpr_clock_graph import ClockGraph
    from ownpr_clock_partition import partition
    p=ROOT/'results/ownpr_uart_pin_eco_001';graph=RoutingGraph(Device(),ROOT/'results/native_rrg_001');model=ClockGraph(ROOT/'results/native_clock_inventory_005')
    nets=read(p/'all_nets.json');tree=read(p/'clock_tree.json');ordinary,_=partition(graph,nets,tree,model)
    assert ordinary==read(p/'nets.json')
    bad=copy.deepcopy(tree);bad['parents'][0]['parent']=[999,999,999]
    with pytest.raises(ValueError,match='parent table'):partition(graph,nets,bad,model)


def test_retained_final_pin_candidate_has_direct_proof_and_no_hold_regression():
    flow=read(ROOT/'results/ownpr_uart_pin_closure_002/manifest.json')
    assert flow['status']=='bounded_rounds_complete' and flow['wall_seconds']<900
    assert [r['status'] for r in flow['rounds']]==['accepted','rejected_global_timing','rejected_global_timing','accepted','accepted']
    assert flow['metrics']['setup_wns_ns']==-.976 and flow['metrics']['hold_wns_ns']==.037
    proof=read(ROOT/'results/ownpr_uart_pin_final_cec_001/proof.json')['proof']
    assert all(p['cec']['equivalent'] for p in proof) and sum(p['observed_hard_inputs_and_ports'] for p in proof)==328
    r=audit(Path(flow['selected']['sta']),Path(flow['selected']['placement']))
    assert r['configuration_bits_verified']==20132 and not r['native_sta_constraints_satisfied']
