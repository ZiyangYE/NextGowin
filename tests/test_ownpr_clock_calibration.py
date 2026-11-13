import sys
from pathlib import Path
from contextlib import nullcontext
import pytest
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
import ownpr_clock_calibration as calibration


def test_retained_clock_queries_are_bound_to_full_native_sta():
    rows,report=calibration.retained_reference()
    assert report['sinks']==73 and report['maximum_endpoint_rounding_error_ns']<=2e-6
    assert len(rows)>73 and report['reference_kind']=='own_clock_samples_checked_against_native_final_sta'


def test_changed_retained_query_file_hash_is_rejected(monkeypatch):
    original=calibration.sha
    monkeypatch.setattr(calibration,'sha',lambda p:'bad' if Path(p).name=='clock_wire_delays.json' else original(p))
    with pytest.raises(ValueError,match='provenance'):calibration.retained_reference()


def test_live_replay_checks_every_corner_and_rejects_a_changed_value(monkeypatch,tmp_path):
    rows=[dict(node=[1,1,1],parent=None,values_by_selector=[[.1,.2,.3,.4]]*3)]
    monkeypatch.setattr(calibration,'ROOT',tmp_path);monkeypatch.setattr(calibration,'retained_reference',lambda:(rows,{'reference_kind':'test'}))
    class Native:
        changed=False
        def resolve_routed_nodes(self,p):return {key:1 for key in p}
        def resolved_parent_tree(self,pointers,parents):return nullcontext()
        def delay_pointer(self,p,s):return [.1,.2,.3,.5 if self.changed and s==2 else .4]
    native=Native();assert calibration.replay(native)['matched']
    native.changed=True
    with pytest.raises(ValueError,match='Standalone clock query'):calibration.replay(native)
