import json
import sys
from pathlib import Path
import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from run_ownpr_ioreg_closure import bind_verified_constraints


def test_verified_contract_fills_missing_metadata_and_rejects_conflicts(tmp_path):
    manifest=tmp_path/'manifest.json'
    manifest.write_text(json.dumps({'status':'placed'}))
    baseline={'cst_sha256':'fixed-pins','source_sdc_sha256':'full-io'}
    bind_verified_constraints(tmp_path,baseline)
    actual=json.loads(manifest.read_text())
    assert actual['cst_sha256']=='fixed-pins' and actual['sdc_sha256']=='full-io'
    previous=manifest.read_bytes()
    with pytest.raises(ValueError,match='constraint differs'):
        bind_verified_constraints(tmp_path,{**baseline,'source_sdc_sha256':'relaxed'})
    assert manifest.read_bytes()==previous
