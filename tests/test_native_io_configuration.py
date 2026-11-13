import copy,json,sys
from pathlib import Path
import pytest
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from audit_native_io_configuration import check_state,audit
from audit_native_encoding_probe import payload


def read(name):return json.loads((ROOT/'results'/name).read_text())


def test_used_io_and_gsr_match_native_reference():
    r=check_state(read('native_ownpr_export_018/io_state_after_encoding.json'),read('native_io_reference_001/io_configuration_state.json'))
    assert r['ports']==19 and r['physical_attributes']==304 and r['gsr_bound']


def test_old_unbound_gsr_fails_even_with_valid_crc():
    with pytest.raises(ValueError,match='GSR'):
        # Align the logical voltage for this focused test; GSR must still fail.
        old=read('native_ownpr_export_016/io_state_after_encoding.json')
        for row in old['io']:row['logical'][10]=93
        check_state(old,read('native_io_reference_001/io_configuration_state.json'))


def test_wrong_physical_drive_is_rejected():
    current=read('native_ownpr_export_018/io_state_after_encoding.json');current['io'][0]['physical'][3]=96
    with pytest.raises(ValueError,match='attribute'):check_state(current,read('native_io_reference_001/io_configuration_state.json'))


def test_initial_gsr_bits_and_physical_readback():
    r=audit(ROOT/'results/native_ownpr_export_018')
    assert r['status']=='initial_io_state_and_gsr_bits_matched' and len(r['gsr_bits'])==2
    assert not r['full_bitstream_verified']


def test_gzip_calibration_payload_has_identical_semantics(tmp_path):
    import gzip
    text='// Calibration only\n0101\n11110000\n';plain=tmp_path/'sample.fs';packed=tmp_path/'sample.fs.gz'
    plain.write_text(text)
    with gzip.open(packed,'wt',encoding='ascii') as stream:stream.write(text)
    assert payload(plain)==payload(packed)==['0101','11110000']


def test_all_initial_io_differentials_and_production_default_separation():
    r=audit(ROOT/'results/native_io_encoding_003')
    assert r['io_differential_cases']==26 and r['changed_bit_observations']==35
    assert r['full_payload_restored'] and r['production_defaults_match_reference']
    assert r['calibration_default_differences']==638
    assert not r['probe_fs_is_production'] and not r['full_bitstream_verified']
