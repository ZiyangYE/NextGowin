import copy,json,sys
from pathlib import Path
import pytest
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from ownpr_ioreg_macros import relocate,validate
from ownpr_ioreg_timing import IoregTimingModel
from audit_ownpr_native_export import audit
from audit_ownpr_joint_routes import audit as joint_audit
from select_ownpr_candidate import check_contract


def read(p):return json.loads(p.read_text())
BASE=ROOT/'results/ownpr_uart_wide_place_003'
PLACE=ROOT/'results/ownpr_uart_ioreg_place_001'
EXPORT=ROOT/'results/native_ownpr_uart_ioreg_export_002'


def test_output_register_relocation_preserves_logic_and_every_other_site():
    design=read(BASE/'packed.json');before=read(BASE/'placement.json');changed,moves,check=relocate(design,before,['rx_valid'])
    assert (BASE/'packed.json').read_bytes()==(PLACE/'packed.json').read_bytes()
    assert check['capacity_verified'] and len(check['records'])==1
    ff=moves[0]['ff'];source=moves[0]['source_lut']
    assert before['clusters'][source]['ff']==ff and changed['clusters'][source]['ff'] is None
    assert changed['fixed_io']==before['fixed_io'] and changed['arithmetic_clusters']==before['arithmetic_clusters']
    assert changed['mux_clusters']==before['mux_clusters']
    for name,row in before['clusters'].items():
        assert changed['clusters'][name]==({**row,'ff':None} if name==source else row)
    assert changed['ioreg_clusters'][ff]['site']==[110,135,0]


def test_output_register_feedback_constant_controls_and_pad_changes_rejected():
    design=read(PLACE/'packed.json');placed=read(PLACE/'placement.json');ff=next(iter(placed['ioreg_clusters']));cell=design['modules']['top']['cells'][ff]
    bad=copy.deepcopy(design);bad['modules']['top']['cells']['extra']={**copy.deepcopy(cell),'type':'LUT1','parameters':{'INIT':'10'},'connections':{'I0':cell['connections']['Q'],'F':[99999]},'port_directions':{'I0':'input','F':'output'}}
    with pytest.raises(ValueError,match='exclusively'):validate(bad,placed)
    bad=copy.deepcopy(design);bad['modules']['top']['cells'][ff]['connections']['CE']=['1']
    with pytest.raises(ValueError,match='Constant'):validate(bad,placed)
    bad=copy.deepcopy(placed);bad['ioreg_clusters'][ff]['site'][2]=1
    with pytest.raises(ValueError,match='pad'):validate(design,bad)
    bad=copy.deepcopy(placed);next(r for r in bad['clusters'].values() if r['ff'] is None)['ff']=ff
    with pytest.raises(ValueError,match='coverage'):validate(design,bad)
    bad=copy.deepcopy(design);bad['modules']['top']['netnames']['q_init']={'bits':cell['connections']['Q'],'attributes':{'init':'1'}}
    with pytest.raises(ValueError,match='initialization'):validate(bad,placed)
    bad=copy.deepcopy(placed);bad['fixed_io'][bad['ioreg_clusters'][ff]['iobuf']]['package_pin']='K18'
    with pytest.raises(ValueError,match='package pin'):validate(design,bad)


def test_output_register_timing_queries_cover_d_ce_async_and_c2q():
    model=IoregTimingModel();assert len(model.values)==21 and model.reference_report['cell_or_constraint_rows']==86
    data=read(EXPORT/'ioreg_timing_queries.json');placed=read(PLACE/'placement.json');mapping=read(EXPORT/'name_map.json')
    assert model.check_capture(data,placed,mapping)['query_vectors']==21
    bad=copy.deepcopy(data);bad['registers'][0]['queries'][0]['values'][2]+=.001
    with pytest.raises(ValueError,match='raw library'):model.check_capture(bad,placed,mapping)
    bad=copy.deepcopy(data);bad['registers'][0]['queries'].pop()
    with pytest.raises(ValueError,match='Incomplete'):model.check_capture(bad,placed,mapping)
    bad=copy.deepcopy(data);bad['registers'][0]['site'][0]=50
    with pytest.raises(ValueError,match='placement'):model.check_capture(bad,placed,mapping)


def test_real_single_ioreg_native_handoff_and_ordinary_clock_partition():
    check=audit(EXPORT,PLACE);t=check['timing'];io=t['ioreg']
    assert io['registers']==1 and io['attributes_verified']==171 and io['internal_net_rows']==2
    assert io['timing_queries']['query_vectors']==21 and io['timing_report']['cell_or_constraint_rows']==8
    assert t['hold_violated_endpoints']==0 and t['reported_fmax_mhz']==177.582 and not check['native_sta_constraints_satisfied']
    assert check['configuration_bits_verified']==21365 and check['io']['ports']==24
    joint=joint_audit(PLACE,ROOT/'results/ownpr_uart_ioreg_eco_001',ROOT/'results/ownpr_uart_ioreg_wire_002')
    assert joint['ioreg_capacity']['capacity_verified'] and joint['clock_sinks']==73


def test_nine_ioregs_have_distinct_pad_blocks_and_original_total_state():
    folder=ROOT/'results/ownpr_uart_ioreg_place_002';design=read(folder/'packed.json');placed=read(folder/'placement.json');r=validate(design,placed)
    assert len(r['records'])==9 and sum(bool(g['ff']) for g in placed['clusters'].values())==64
    assert len({tuple(row['site']) for row in r['records']})==9
    assert (BASE/'packed.json').read_bytes()==(folder/'packed.json').read_bytes()
    meta=read(ROOT/'results/ownpr_uart_ioreg_route_002/manifest.json')
    assert meta['status']=='routed' and meta['reserved_clock_and_output_nodes']>=82


def test_internal_clock_changes_require_fixed_source_and_original_io_contract():
    before={'cst_sha256':'io','source_sdc_sha256':'timing','clock_tree_sha256':'old'};after={**before,'clock_tree_sha256':'new'}
    tree={'source':[85,182,38]}
    with pytest.raises(ValueError,match='frozen'):check_contract(before,after,tree,tree)
    check_contract(before,after,tree,tree,True)
    with pytest.raises(ValueError,match='source'):check_contract(before,after,tree,{'source':[85,182,39]},True)
    with pytest.raises(ValueError,match='constraint'):check_contract(before,{**after,'cst_sha256':'changed'},tree,tree,True)


def test_bounded_end_to_end_ioreg_attempt_reproduces_verified_nine_register_result():
    folder=ROOT/'results/ownpr_uart_ioreg_closure_001';flow=read(folder/'manifest.json')
    assert flow['status']=='bounded_ioreg_attempt_complete' and flow['wall_seconds']<flow['budget_seconds']<=3600
    assert len(flow['stages'])==14 and all(r['status']=='passed' for r in flow['stages'])
    assert flow['logical_netlist_byte_identical'] and not flow['vendor_placement_executed'] and not flow['vendor_routing_executed']
    assert flow['metrics']['reported_fmax_mhz']==179.031 and flow['metrics']['hold_violations']==0 and flow['metrics']['setup_violations']==44
    assert [r['decision'] for r in flow['candidates']]==['rejected_global_timing','accepted']
    check=audit(Path(flow['selected']['sta']),Path(flow['selected']['placement']))
    assert check['configuration_bits_verified']==22042 and check['timing']['ioreg']['registers']==9
    assert check['timing']['ioreg']['timing_queries']['query_vectors']==189 and check['timing']['ioreg']['timing_report']['cell_or_constraint_rows']==72
    assert not check['native_sta_constraints_satisfied']
