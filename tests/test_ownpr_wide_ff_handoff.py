import copy,hashlib,json,sys
from pathlib import Path
import pytest
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
import audit_ownpr_wide_ff as selected
from audit_ownpr_native_export import audit

NATIVE=ROOT/'results/tmds_wide_ff_trial_001/export/native'
PLACE=ROOT/'results/tmds_wide_ff_place_001'


def test_complete_handoff_contains_selected_ff_gate_and_keeps_failed_timing():
    result=audit(NATIVE,PLACE)
    assert result['status']=='initial_handoff_checks_passed' and result['io']['ports']==23
    assert not result['native_sta_constraints_satisfied']
    assert result['timing']['setup_violated_endpoints']==2 and result['timing']['hold_violated_endpoints']==0
    row=result['timing']['wide_ff']['records'][0]
    assert row['level']==5 and row['chain']==[[98,166,295],[98,166,48]]
    # Loaded endpoint timing is not silently substituted with isolated zero.
    assert max(row['wire_delays'][1][2:])>0
    binding=selected.load_json(NATIVE/'binding_report.json');stable={k:binding[k] for k in ('bindings','encoding_preserved_parents','encoding_preserved_geometry')}
    assert result['timing']['wide_ff']['binding_geometry_sha256']==hashlib.sha256(json.dumps(stable,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def test_native_pair_substitution_is_rejected(monkeypatch):
    read=selected.load_json
    def changed(path):
        data=read(path)
        if Path(path).name=='sta_endpoint_audit.json':
            data=copy.deepcopy(data)
            for row in data:
                if row['instance']=='$auto$ff.cc:337:slice$1645' and row['pin']=='D':row['direct_connection']=True;row['nodes']=[]
        return data
    monkeypatch.setattr(selected,'load_json',changed)
    with pytest.raises(ValueError,match='LUT-paired bypass'):selected.audit(NATIVE,PLACE)


def test_generated_report_can_be_checked_before_sta_completed_flag(monkeypatch):
    read=selected.load_json
    def preaudit(path):
        data=read(path)
        if Path(path)==NATIVE/'manifest.json':
            data=copy.deepcopy(data);data['sta_completed']=False
        return data
    monkeypatch.setattr(selected,'load_json',preaudit)
    assert selected.audit(NATIVE,PLACE)['status']=='wide_ff_native_endpoints_and_parents_matched'
