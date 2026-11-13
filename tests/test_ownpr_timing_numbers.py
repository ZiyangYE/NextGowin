import json,math,re,sys
from pathlib import Path
import pytest
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from ownpr_timing_numbers import negative_slack


def test_negative_zero_is_not_a_pass_and_nonfinite_slack_is_rejected():
    assert negative_slack('-0.000') and negative_slack(-0.0) and negative_slack('-.001')
    assert not negative_slack('0.000') and not negative_slack(.001)
    for value in ('nan','inf','-inf'):
        with pytest.raises(ValueError,match='Nonfinite'):negative_slack(value)


def test_actual_failed_export_has_21_violations_including_one_negative_zero():
    from audit_ownpr_extended_sta import audit
    from ownpr_route_eco import violations
    from ownpr_eco_feedback import setup_paths
    p=ROOT/'results/ownpr_uart_arrival_reorder_closure_002/round_001';sta=p/'eco_export/native'
    # The original failed artifact is retained; this parses its complete report,
    # rather than modifying its failure manifest or claiming new signoff.
    checked=audit(sta,p/'placement');text=(sta/'probe.tr').read_text()
    assert checked['coverage']['Setup']['violating_paths']==21
    rows=violations(text,'Setup')
    assert len(rows)==21 and sum(v==0 and math.copysign(1.,v)<0 for v,a,b in rows)==1
    paths=setup_paths(text,checked['coverage']['Setup']['endpoints'])
    assert len(paths)==21 and any(p['slack_ns']==0 and negative_slack(p['slack_ns']) for p in paths)


def test_only_negative_zero_feedback_has_finite_criticality(tmp_path,monkeypatch):
    from ownpr_place import sha
    from ownpr_placement_feedback import load
    import ownpr_native_netlist
    text='3.3.1 Setup Analysis Report\nSlack : -0.000\nData Arrival Path:\n 1.000 0.100 tNET RR 1 R1C1 own_i_0000/I0\nData Required Path:\n'
    (tmp_path/'probe.tr').write_text(text);(tmp_path/'probe.vg').write_text('fixture')
    mapping={'instances':{'lut':'own_i_0000'}};(tmp_path/'name_map.json').write_text(json.dumps(mapping))
    check=dict(source_sha256={'probe.tr':sha(tmp_path/'probe.tr')},timing=dict(setup_violated_endpoints=1,path_coverage={'setup':{'worst_slack_ns':-0.0}}))
    (tmp_path/'handoff_audit.json').write_text(json.dumps(check))
    meta=dict(status='imported',sta_completed=True,vendor_placement_executed=False,vendor_routing_executed=False,handoff_audit_sha256=sha(tmp_path/'handoff_audit.json'))
    (tmp_path/'manifest.json').write_text(json.dumps(meta));monkeypatch.setattr(ownpr_native_netlist,'emit',lambda d,hard=None:('fixture',mapping))
    weights,info=load(tmp_path,{'modules':{'top':{'cells':{'lut':{'type':'LUT1','port_directions':{'I0':'input'}}}}}})
    assert weights=={('lut','I0'):1.} and info['paths']==1


def test_negative_zero_pulse_width_is_explicitly_not_satisfied():
    from audit_ownpr_native_export import timing_check
    folder=ROOT/'results/native_ownpr_export_012';placement=ROOT/'results/ownpr_int2float_place_004'
    text=(folder/'probe.tr').read_text();section=re.search(r'^3\.2 Minimum Pulse Width Table\n(.*?)(?=\n3\.)',text,re.S|re.M)
    changed=re.sub(r'(?m)^(\s*1\s+)[-\d.]+(\s+[\d.]+\s+[\d.]+\s+(?:High|Low) Pulse Width)',r'\g<1>-0.000\2',section[1],count=1)
    assert changed!=section[1]
    text=text[:section.start(1)]+changed+text[section.end(1):]
    result=timing_check(json.loads((folder/'sta_endpoint_audit.json').read_text()),text,
        json.loads((placement/'packed.json').read_text()),json.loads((placement/'placement.json').read_text()))
    assert result['minimum_pulse_width_slack_ns']==0 and not result['minimum_pulse_width_passed']
