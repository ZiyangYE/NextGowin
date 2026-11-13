import sys,copy
from pathlib import Path
from types import SimpleNamespace
import pytest
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from ownpr_carry_lut_sharing import calibration,check,attributes
from ownpr_alu_configuration import Mode2Configuration

def fixture():
    a=calibration();d={'modules':{'top':{'cells':{'lut':{'type':'LUT4'}}}}};p={'mixed_carry_lut_calibration':a['capture_sha256'],'clusters':{'lut':dict(lut='lut',ff=None,row=1,column=1,slot=6)}}
    dev=SimpleNamespace(grid=[[17]*3 for _ in range(3)],fse=SimpleNamespace(sha256=a['fse_sha256']))
    return d,p,dev

def test_admission_is_limited_to_two_unpaired_spare_slots():
    d,p,dev=fixture()
    assert check(d,p,dev,p['clusters']['lut'])==calibration()['capture_sha256']
    for field,value in [('slot',5),('slot',8),('ff','register'),('wide_ff_root','wide')]:
        q=copy.deepcopy(p);q['clusters']['lut'][field]=value
        with pytest.raises(ValueError):check(d,q,dev,q['clusters']['lut'])
    q=copy.deepcopy(p);q.pop('mixed_carry_lut_calibration')
    with pytest.raises(ValueError):check(d,q,dev,q['clusters']['lut'])

def test_mixed_defaults_are_retained_and_legacy_alu_encoding_stays_identical():
    d,p,dev=fixture();mixed,shared=attributes(p,1,1,range(6));assert shared
    assert mixed[3][2]==15 and all(x[0]==10 for x in mixed[:3])
    p['clusters']={};legacy,shared=attributes(p,1,1,range(6));assert not shared
    assert legacy==Mode2Configuration.with_alu_modes([[0]*19 for _ in range(4)],range(6))

def test_two_site_native_capture_checks_full_bitmap_and_pin_composition():
    a=calibration()
    assert len(a['sites'])==2 and a['ordinary_bitmap_composition_match'] and a['full_payload_restored']
    assert not a['ff_sharing_supported'] and not a['full_route_sta_verified']
