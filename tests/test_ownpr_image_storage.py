import gzip,hashlib,sys
from pathlib import Path
import pytest
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from ownpr_image_storage import compact,sha,resolve
from audit_native_encoding_probe import payload


def test_compression_preserves_raw_hash_and_payload(tmp_path):
    p=tmp_path/'probe.fs';data=b'// native image\r\n010101\r\n';p.write_bytes(data);digest=sha(p);record=compact(p)
    assert not p.exists() and resolve(p)==p.with_suffix('.fs.gz')
    assert sha(p)==digest==record['original_sha256'] and payload(p)==['010101']
    assert sha(resolve(p))==hashlib.sha256(resolve(p).read_bytes()).hexdigest()


def test_missing_corrupt_or_ambiguous_archives_do_not_silently_pass(tmp_path):
    p=tmp_path/'probe.fs'
    with pytest.raises(FileNotFoundError):sha(p)
    p.with_suffix('.fs.gz').write_bytes(b'broken')
    with pytest.raises((OSError,EOFError)):sha(p)
    p.write_bytes(b'01\n')
    with pytest.raises(FileExistsError):compact(p)


def test_large_json_alias_preserves_bytes_and_can_materialize_for_native_worker(tmp_path):
    from ownpr_image_storage import compact_json,load_json,copy_original,available
    p=tmp_path/'routes.json';raw=b'{"nets": [1, 2], "qualified": false}\r\n';p.write_bytes(raw);digest=sha(p)
    compact_json(p)
    assert not p.exists() and available(p) and sha(p)==digest
    assert load_json(p)=={'nets':[1,2],'qualified':False}
    target=tmp_path/'materialized.json';copy_original(p,target)
    assert target.read_bytes()==raw


def test_corrupt_json_archive_is_not_accepted(tmp_path):
    from ownpr_image_storage import load_json
    p=tmp_path/'routes.json';p.with_suffix('.json.gz').write_bytes(b'not gzip')
    with pytest.raises((OSError,EOFError)):load_json(p)


def test_archived_timing_report_preserves_hash_and_normal_text_semantics(tmp_path):
    from ownpr_image_storage import read_text,compact_text,copy_original
    file=tmp_path/'probe.tr';data=b'Path1\r\nSlack: -0.000\r\n';file.write_bytes(data);digest=sha(file);text=read_text(file)
    compact_text(file)
    assert sha(file)==digest and read_text(file)==text=='Path1\nSlack: -0.000\n'
    copy_original(file,tmp_path/'restored.tr');assert (tmp_path/'restored.tr').read_bytes()==data


def test_html_storage_preserves_exact_bytes_for_legacy_materialization(tmp_path):
    from ownpr_image_storage import compact_html,read_text,copy_original
    file=tmp_path/'report.html';data=b'<html>\r\nSTA &amp; IO\r\n</html>';file.write_bytes(data);before=sha(file)
    compact_html(file)
    assert sha(file)==before and read_text(file)=='<html>\nSTA &amp; IO\n</html>'
    copy_original(file,tmp_path/'restored.html');assert (tmp_path/'restored.html').read_bytes()==data


def test_rtlil_storage_preserves_formal_model_bytes(tmp_path):
    from ownpr_image_storage import compact_rtlil,read_text,copy_original
    file=tmp_path/'product.il';data=b'# proof\r\nmodule \\top\r\nend\r\n';file.write_bytes(data);before=sha(file)
    compact_rtlil(file)
    assert sha(file)==before and read_text(file)==data.decode().replace('\r\n','\n')
    copy_original(file,tmp_path/'restored.il');assert (tmp_path/'restored.il').read_bytes()==data
