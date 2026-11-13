import copy,sys
from pathlib import Path
import pytest
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from ownpr_macro_replication import replicate
from ownpr_image_storage import load_json
from ownpr_device import Device
from ownpr_mux_macros import validate


def test_wide_copy_preserves_each_truth_table_internal_link_and_state_boundary():
    base=ROOT/'results/tmds_feedback_moves_002/round_003/placement';d=load_json(base/'packed.json');p=load_json(base/'placement.json');dev=Device();before=copy.deepcopy(d)
    new,layout,change=replicate(d,p,dev,'ng_coopt_72',[('ng_coopt_127','S0')]);cells=new['modules']['top']['cells'];original=d['modules']['top']['cells'];reverse={v:int(k) for k,v in change['bit_map'].items()}
    assert d==before and new['modules']['top']['ports']==d['modules']['top']['ports']
    assert change['lut_growth']==4 and change['mux_growth']==3 and len(change['members'])==7
    for old,name in change['members'].items():
        restored=copy.deepcopy(cells[name]);restored['connections']={pin:[reverse.get(b,b) for b in bs] for pin,bs in restored['connections'].items()};assert restored==original[old]
    for name,c in original.items():
        restored=copy.deepcopy(cells[name]);restored['connections']={pin:[reverse.get(b,b) for b in bs] for pin,bs in restored['connections'].items()};assert restored==c
    assert layout['fixed_io']==p['fixed_io'] and all(layout['clusters'][n]==r for n,r in p['clusters'].items())
    assert all(layout['mux_clusters'][n]==r for n,r in p['mux_clusters'].items())
    old_audit=validate(d,p,dev);new_audit=validate(new,layout,dev)
    assert len(new_audit['macros'])==len(old_audit['macros'])+1 and len(new_audit['links'])==len(old_audit['links'])+6
    source_bits={b for c in original.values() for bs in c['connections'].values() for b in bs}
    assert not set(reverse)&source_bits


def test_partial_wide_macro_or_dedicated_input_cannot_be_replicated():
    base=ROOT/'results/tmds_feedback_moves_002/round_003/placement';d=load_json(base/'packed.json');p=load_json(base/'placement.json');dev=Device()
    with pytest.raises(ValueError,match='complete'):replicate(d,p,dev,'ng_coopt_70',[('ng_coopt_127','S0')])
    with pytest.raises(ValueError,match='selector edge'):replicate(d,p,dev,'ng_coopt_72',[('ng_coopt_127','I0')])
    with pytest.raises(ValueError,match='duplicate'):replicate(d,p,dev,'ng_coopt_72',[('ng_coopt_127','S0')]*2)


def test_compressed_native_clock_topology_retains_provenance_checks(tmp_path):
    import json,gzip
    from ownpr_clock_graph import ClockGraph
    from ownpr_image_storage import compact_json,sha
    from capture_runtime_tables import EXPECTED
    p=tmp_path/'clock_topology.json';data=dict(nodes=[dict(id=0,coordinate=[1,1,1]),dict(id=1,coordinate=[1,1,2])],edges=[[0,1]])
    p.write_text(json.dumps(data));digest=sha(p)
    (tmp_path/'manifest.json').write_text(json.dumps(dict(status='captured',dll_sha256=EXPECTED,open_chip_database_used=False,vendor_pnr_executed=False,topology_sha256=digest)))
    compact_json(p);graph=ClockGraph(tmp_path);assert graph.sha256==digest and graph.audit_paths([[[1,1,2],[1,1,1]]])['status']=='clock_paths_match'
    data['edges']=[]
    with gzip.open(p.with_name(p.name+'.gz'),'wt') as f:json.dump(data,f)
    with pytest.raises(ValueError,match='Unverified'):ClockGraph(tmp_path)


def test_selecting_every_macro_load_uses_zero_growth_relocation():
    base=ROOT/'results/tmds_feedback_moves_002/round_003/placement';d=load_json(base/'packed.json');p=load_json(base/'placement.json');cells=d['modules']['top']['cells'];bit=cells['ng_coopt_72']['connections']['O'][0]
    sinks=[(n,pin) for n,c in cells.items() for pin,bits in c['connections'].items() if c['port_directions'][pin]=='input' and bit in bits]
    new,layout,change=replicate(d,p,Device(),'ng_coopt_72',sinks)
    assert new==d and len(layout['wide_macros'])==len(p['wide_macros'])
    assert change['kind']=='whole_wide_macro_relocation' and change['lut_growth']==change['mux_growth']==0
    assert change['old_bit']==change['new_bit'] and layout['fixed_io']==p['fixed_io']


def test_area_policy_retains_hard_constraints_and_allows_actual_closure():
    from run_ownpr_cluster_swap import accept_resource_gain
    before=dict(setup_wns_ns=.526,hold_wns_ns=.201,hold_violations=0,setup_violations=0,total_negative_setup_slack_ns=0,other_checks_passed=True,native_constraints_satisfied=True)
    after=dict(before,setup_wns_ns=.527)
    assert accept_resource_gain(before,after,4)==(False,'rejected_area_efficiency')
    assert accept_resource_gain(before,after,4,0)[0]
    assert accept_resource_gain(before,dict(after,hold_wns_ns=-.001,hold_violations=1),4,0)==(False,'rejected_global_timing')
    assert accept_resource_gain(dict(before,setup_wns_ns=-.001,setup_violations=1,native_constraints_satisfied=False),dict(after,setup_wns_ns=.001),4)[0]


def test_archiving_mux_sta_preserves_all_extracted_arcs_and_source_hash(tmp_path):
    from ownpr_image_storage import copy_original,compact_text,sha
    from extract_timing import extract
    folder=ROOT/'results/tmds_102_6mhz_001/export/native';report=tmp_path/'probe.tr'
    copy_original(folder/'probe.tr',report);before=extract(report,folder/'probe.vg');compact_text(report)
    after=extract(report,folder/'probe.vg')
    assert after==before and after['report_sha256']==sha(folder/'probe.tr')
    assert any(r['cell'].startswith('MUX2_LUT') for r in after['arcs'])
