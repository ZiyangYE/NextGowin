import gzip,hashlib,sys,stat
from pathlib import Path
import pytest
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from ownpr_clock_images import compact,image_path,image_sha
from audit_native_encoding_probe import payload


def test_lossless_clock_storage_preserves_original_hash_and_payload(tmp_path):
    original=b'// original timestamp\r\n01010101\r\n11110000\r\n'
    p=tmp_path/'encoding_baseline.fs';p.write_bytes(original);p.chmod(stat.S_IREAD)
    expected=hashlib.sha256(original).hexdigest();before=payload(p);result=compact(tmp_path)
    assert not p.exists() and result['files'][0]['original_sha256']==expected
    compressed=image_path(tmp_path,'encoding_baseline.fs')
    assert image_sha(compressed)==expected and payload(compressed)==before
    assert gzip.decompress(compressed.read_bytes())==original


def test_existing_compressed_output_is_never_overwritten(tmp_path):
    p=tmp_path/'encoding_baseline.fs';p.write_bytes(b'1010\n');q=tmp_path/'encoding_baseline.fs.gz';q.write_bytes(gzip.compress(b'0000\n'))
    with pytest.raises(FileExistsError):compact(tmp_path)
    assert p.read_bytes()==b'1010\n' and image_path(tmp_path,p.name)==p
    assert image_sha(p)!=image_sha(q)


def test_corrupt_compressed_image_fails_instead_of_becoming_empty(tmp_path):
    p=tmp_path/'encoding_clock.fs.gz';p.write_bytes(b'invalid compressed capture')
    with pytest.raises((OSError,EOFError)):image_sha(image_path(tmp_path,'encoding_clock.fs'))
    with pytest.raises(FileNotFoundError):image_path(tmp_path,'encoding_restored.fs')
