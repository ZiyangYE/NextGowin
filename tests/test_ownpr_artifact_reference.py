import gzip,hashlib,json,sys
from pathlib import Path
import pytest
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
import ownpr_image_storage as storage

def test_raw_and_compressed_references_preserve_decoded_hash_and_restore(tmp_path,monkeypatch):
    monkeypatch.setattr(storage,'ROOT',tmp_path);data=b'{"value":17}\n';target=tmp_path/'target.json'
    with gzip.open(str(target)+'.gz','wb') as f:f.write(data)
    source=tmp_path/'copy.json';source.write_bytes(data)
    record=storage.make_reference(source,target)
    assert not source.exists() and storage.available(source)
    assert storage.load_json(source)=={'value':17} and storage.sha(source)==hashlib.sha256(data).hexdigest()
    restored=tmp_path/'restored.json';storage.copy_original(source,restored);assert restored.read_bytes()==data
    second=tmp_path/'second.json';second.write_bytes(data);storage.make_reference(second,source)
    assert storage.sha(second)==storage.sha(target)
    assert storage.sha(Path(str(target)+'.gz'))==hashlib.sha256(Path(str(target)+'.gz').read_bytes()).hexdigest()

def test_reference_rejects_tamper_cycles_and_workspace_escape(tmp_path,monkeypatch):
    monkeypatch.setattr(storage,'ROOT',tmp_path);a=tmp_path/'a.json';b=tmp_path/'b.json';a.write_text('{}');b.write_text('{}');storage.make_reference(a,b)
    b.write_text('{"tampered":1}')
    with pytest.raises(ValueError,match='hash changed'):storage.load_json(a)
    ref=Path(str(a)+'.ref.json');r=json.loads(ref.read_text());r['target']='a.json';ref.write_text(json.dumps(r))
    with pytest.raises(ValueError,match='cyclic'):storage.sha(a)
    r['target']='../escape.json';ref.write_text(json.dumps(r))
    with pytest.raises(ValueError,match='leaves workspace'):storage.sha(a)

def test_reference_never_discards_different_content(tmp_path,monkeypatch):
    monkeypatch.setattr(storage,'ROOT',tmp_path);a=tmp_path/'a.json';b=tmp_path/'b.json';a.write_text('{"a":1}');b.write_text('{"a":2}')
    with pytest.raises(ValueError,match='differs'):storage.make_reference(a,b)
    assert a.read_text()=='{"a":1}' and not Path(str(a)+'.ref.json').exists()

def test_reference_cannot_replace_its_own_canonical_target(tmp_path,monkeypatch):
    monkeypatch.setattr(storage,'ROOT',tmp_path);a=tmp_path/'a.json';b=tmp_path/'b.json';a.write_text('{}');b.write_text('{}')
    storage.make_reference(b,a)
    with pytest.raises(ValueError,match='depends'):storage.make_reference(a,b)
    assert a.read_text()=='{}' and storage.load_json(b)=={}
