import copy,json,random,sys
from pathlib import Path
import pytest
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from ownpr_wide_cone import replace_pair
from ownpr_mux_architecture import MuxArchitecture,children,report_audit
from ownpr_mux_macros import tree,validate
from ownpr_device import Device
from audit_native_mux_encoding import audit as encoding_audit
from audit_ownpr_native_export import audit as export_audit
from reorder_ownpr_wide_inputs import reorder

PLACE=ROOT/'results/ownpr_uart_wide_place_001'
EXPORT=ROOT/'results/native_ownpr_uart_wide_export_002'
def read(p):return json.loads(p.read_text())


def lut(inputs,output,truth):
    return dict(type='LUT'+str(len(inputs)),parameters={'INIT':format(truth,f'0{1<<len(inputs)}b')},
        connections={**{f'I{i}':[b] for i,b in enumerate(inputs)},'F':[output]},
        port_directions={**{f'I{i}':'input' for i in range(len(inputs))},'F':'output'})


def value(design,bit,inputs):
    cells=design['modules']['top']['cells'];drivers={bs[0]:c for c in cells.values() for p,bs in c['connections'].items() if c['port_directions'][p]=='output'}
    known=dict(inputs)
    def get(b):
        if b in known:return known[b]
        c=drivers[b];pins=c['connections']
        if c['type'].startswith('LUT'):
            address=sum(get(pins[f'I{i}'][0])<<i for i in range(int(c['type'][-1])))
            v=(int(c['parameters']['INIT'],2)>>address)&1
        else:v=get(pins['I1' if get(pins['S0'][0]) else 'I0'][0])
        known[b]=v;return v
    return get(bit)


@pytest.mark.parametrize('width',[5,6,7])
def test_shannon_tree_preserves_every_assignment(width):
    rng=random.Random(width)
    for _ in range(24):
        design={'modules':{'top':{'cells':{'parent':lut(list(range(4)),100,rng.getrandbits(16)),
            'root':lut([100]+list(range(4,width)),101,rng.getrandbits(1<<(width-3)))}}}}
        try:changed,info=replace_pair(design,'parent','root')
        except ValueError as error:
            # Random truth tables can have fewer than five functional inputs.
            assert 'independent inputs' in str(error) or 'dependent logic cone' in str(error)
            continue
        assert changed['modules']['top']['cells']['parent']==design['modules']['top']['cells']['parent']
        for stimulus in range(1<<width):
            inputs={i:(stimulus>>i)&1 for i in range(width)}
            assert value(changed,101,inputs)==value(design,101,inputs)
        assert len(tree(changed,'root')['leaves'])==1<<(info['cut_width']-4)


def test_higher_mux_data_order_is_reversed_and_lut8_rejected():
    assert children(6,1)==[('MUX2_LUT5',2),('MUX2_LUT5',0)]
    assert children(7,3)==[('MUX2_LUT6',5),('MUX2_LUT6',1)]
    for level,slot in ((8,7),(6,0),(5,1)):
        with pytest.raises(ValueError,match='Unsupported'):children(level,slot)
    design=read(ROOT/'results/ownpr_uart_wide_cone_003/packed.json');spec=tree(design,'ng_acd_147')
    assert [spec['leaves'][f'own_wide_ng_acd_147_leaf_{i}'] for i in range(8)]==[6,7,4,5,2,3,0,1]


@pytest.mark.parametrize('width',[5,6,7])
def test_wide_input_promotion_preserves_all_assignments_and_existing_leaf_permutations(width):
    from ownpr_lut_pin_eco import permute
    rng=random.Random(100+width)
    for _ in range(8):
        original={'modules':{'top':{'cells':{'parent':lut(list(range(4)),100,rng.getrandbits(16)),
            'root':lut([100]+list(range(4,width)),101,rng.getrandbits(1<<(width-3)))}}}}
        try:design,info=replace_pair(original,'parent','root')
        except ValueError:continue
        first=next(iter(tree(design,'root')['leaves']));cells=design['modules']['top']['cells']
        cells[first]=permute(cells[first],0,3)
        spec=tree(design,'root')
        # Random truth tables can lose an independent input during composition.
        for target in range(4,info['cut_width']):
            selector=next(n for n,r in spec['muxes'].items() if r['level']==target+1)
            for bit in info['cut_bits']:
                if bit==cells[selector]['connections']['S0'][0]:continue
                changed,record=reorder(design,'root',bit,target)
                assert record['new_order'][target]==bit and record['target_level']==target+1
                assert set(changed['modules']['top']['cells'])==set(cells)
                for name,c in cells.items():
                    for pin,direction in c['port_directions'].items():
                        if direction=='output':assert changed['modules']['top']['cells'][name]['connections'][pin]==c['connections'][pin]
                for stimulus in range(1<<width):
                    inputs={i:(stimulus>>i)&1 for i in range(width)}
                    assert value(changed,101,inputs)==value(original,101,inputs)


def test_wide_reorder_rejects_missing_input_and_already_final_selector():
    design=read(ROOT/'results/ownpr_uart_ce_wide_place_002/packed.json')
    with pytest.raises(ValueError,match='not a wide-cut input'):reorder(design,'ng_acd_147',999999)
    with pytest.raises(ValueError,match='already'):reorder(design,'ng_acd_147',297)
    for target in (3,7,False,4.5):
        with pytest.raises(ValueError,match='selector index'):reorder(design,'ng_acd_147',265,target)


def test_shared_internal_output_and_bad_physical_sites_are_rejected():
    design=read(PLACE/'packed.json');placed=read(PLACE/'placement.json');d=Device()
    assert len(validate(design,placed,d)['links'])==6
    broken=copy.deepcopy(design);cells=broken['modules']['top']['cells'];bit=cells['ng_acd_27']['connections']['I0'][0]
    cells['extra']=lut([bit],999,2)
    with pytest.raises(ValueError,match='private'):tree(broken,'ng_acd_27')
    for r,c in ((-1,0),(0,-1),(len(d.grid),0)):
        bad=copy.deepcopy(placed);bad['wide_macros'][0].update(row=r,column=c)
        with pytest.raises(ValueError,match='outside'):validate(design,bad,d)
    bad=copy.deepcopy(placed);bad['mux_clusters']['ng_acd_27']['slot']=5
    with pytest.raises(ValueError,match='physical slots'):validate(design,bad,d)
    bad=copy.deepcopy(placed);leaf=next(iter(tree(design,'ng_acd_27')['leaves']));bad['clusters'][leaf]['ff']='illegal'
    with pytest.raises(ValueError,match='uncalibrated FF'):validate(design,bad,d)


@pytest.fixture(scope='module')
def architecture():return MuxArchitecture()


def test_standalone_delays_match_completed_native_data_and_actual_edges(architecture):
    assert len(architecture.bindings)==63 and architecture.report['internal_net_rows']>0
    assert architecture.delay(17,5,0,'I0',1)[2]==pytest.approx(.135)
    assert architecture.delay(17,6,1,'I0',1)[2]==pytest.approx(.077625)
    assert architecture.delay(17,7,3,'S0',1)[2]==pytest.approx(.22725)
    data=read(EXPORT/'mux_timing_queries.json');design=read(PLACE/'packed.json');placement=read(PLACE/'placement.json');mapping=read(EXPORT/'name_map.json')
    assert architecture.check_capture(data,design,placement,mapping)['direct_timing_edges']==6
    for mutation in ('missing_edge','wrong_delay','reversed_input','duplicate_edge'):
        bad=copy.deepcopy(data);row=bad['muxes'][0]
        if mutation=='missing_edge':row['links'][0]['timing_edge_present']=False
        elif mutation=='wrong_delay':row['queries'][0]['values'][0]+=.01
        elif mutation=='reversed_input':row['links'][0]['source']=row['links'][1]['source']
        else:row['links'].append(copy.deepcopy(row['links'][0]))
        with pytest.raises(ValueError):architecture.check_capture(bad,design,placement,mapping)


def test_worst_path_report_does_not_claim_unreported_internal_edges():
    with pytest.raises(ValueError,match='Empty mux'):report_audit(EXPORT)
    report=report_audit(EXPORT,require_internal_rows=False)
    assert report['cell_arcs']==13 and report['internal_net_rows']==0
    assert not report['internal_edges_covered_by_report']


def test_wide_encoding_has_positive_cls_controls_and_full_restore():
    report=encoding_audit(ROOT/'results/native_mux_encoding_003')
    assert [r['cls_default_activation_bits'] for r in report['records']]==[3,6,12]
    assert all(r['extra_mux_binding_bits']==0 and r['full_payload_restored'] for r in report['records'])
    assert not report['full_bitstream_verified']


def test_actual_wide_netlist_cec_and_native_export_keep_all_io():
    proof=read(ROOT/'results/ownpr_uart_wide_cone_001/manifest.json')['proof']
    assert proof and all(p['cec']['equivalent'] for p in proof)
    r=export_audit(EXPORT,PLACE)
    assert r['connectivity']['mux_functions']==3 and r['connectivity']['lut_functions']==140
    assert r['io']['ports']==24 and r['configuration_bits_verified']==20392
    assert r['timing']['mux_queries']['query_vectors']==27
    assert r['timing']['hold_violated_endpoints']==8 and not r['native_sta_constraints_satisfied']
    meta=read(EXPORT/'manifest.json')
    assert not meta['vendor_placement_executed'] and not meta['vendor_routing_executed']


def test_combined_wide_macros_keep_ff_sites_and_verify_both_mux_trees(architecture):
    base=read(ROOT/'results/ownpr_uart_pin_closure_002/round_005/eco/placement/placement.json')
    place=ROOT/'results/ownpr_uart_wide_place_003';folder=ROOT/'results/native_ownpr_uart_wide_export_007'
    design=read(place/'packed.json');placed=read(place/'placement.json')
    assert base['fixed_io']==placed['fixed_io'] and base['arithmetic_clusters']==placed['arithmetic_clusters']
    assert {n:r for n,r in base['clusters'].items() if r['ff']}=={n:r for n,r in placed['clusters'].items() if r['ff']}
    q=architecture.check_capture(read(folder/'mux_timing_queries.json'),design,placed,read(folder/'name_map.json'))
    assert q['muxes']==10 and q['direct_timing_edges']==20 and q['query_vectors']==90
    assert report_audit(folder)['internal_net_rows']==1
    check=read(folder/'handoff_audit.json')
    assert check['configuration_bits_verified']==21189 and check['timing']['hold_violated_endpoints']==0
    assert check['timing']['setup_violated_endpoints']==46 and not check['native_sta_constraints_satisfied']


def test_global_wide_selection_rejects_hold_failures_and_proves_selected_logic():
    summary=read(ROOT/'results/ownpr_uart_wide_summary_001/summary.json')
    assert len(summary['candidates'])==6 and summary['fixed_io_and_source_sdc_preserved']
    for c in summary['candidates']:
        if c['metrics']['hold_violations']:assert c['decision']=='rejected_global_timing'
    assert summary['metrics']['hold_violations']==0 and summary['metrics']['setup_wns_ns']==-.844
    assert summary['metrics']['reported_fmax_mhz']==171.107 and not summary['metrics']['native_constraints_satisfied']
    proof=summary['direct_selected_cec']
    assert all(p['cec']['equivalent'] for p in proof) and sum(p['observed_hard_inputs_and_ports'] for p in proof)==328
    last=summary['worst_setup_path']['rows'][-1]
    assert last['port']=='rx_valid' and last['package_pin']=='E26' and last['delay_ns']==2.5
