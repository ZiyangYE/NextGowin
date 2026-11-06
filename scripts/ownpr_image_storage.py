"""Lossless FS storage with transparent reads and original uncompressed hashes."""
import gzip,hashlib,shutil,stat,json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]


def resolve(path):return _resolve(Path(path),set())


def _digest(requested,actual):
    opener=gzip.open if actual.suffix=='.gz' and requested.suffix!='.gz' else open
    with opener(actual,'rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()


def _resolve(path,seen):
    path=Path(path)
    if path.is_file():return path
    compressed=path.with_name(path.name+'.gz')
    if path.suffix in ('.fs','.json','.tr') and compressed.is_file():return compressed
    reference=path.with_name(path.name+'.ref.json')
    if path.suffix in ('.fs','.json','.tr') and reference.is_file():
        absolute=path.resolve();root=ROOT.resolve()
        if not absolute.is_relative_to(root) or absolute in seen or len(seen)>=16:raise ValueError('Invalid or cyclic artifact reference')
        row=json.loads(reference.read_text(encoding='utf-8'))
        if set(row)!={'schema','target','sha256'} or row['schema']!='nextgowin-artifact-reference-v1':raise ValueError('Invalid artifact reference schema')
        target=(root/row['target']).resolve()
        if not target.is_relative_to(root) or target.suffix!=path.suffix:raise ValueError('Artifact reference leaves workspace or changes type')
        actual=_resolve(target,seen|{absolute})
        if _digest(target,actual)!=row['sha256']:raise ValueError('Referenced artifact hash changed')
        return actual
    raise FileNotFoundError(path)


def sha(path):
    requested=Path(path);actual=resolve(requested)
    # Explicit .gz references retain their original compressed-file hash
    # convention. An archived .fs reference hashes its decoded original bytes.
    return _digest(requested,actual)


def available(path):
    try:resolve(path);return True
    except FileNotFoundError:return False


def load_json(path):
    actual=resolve(path)
    with (gzip.open(actual,'rt',encoding='utf-8') if actual.suffix=='.gz' else actual.open(encoding='utf-8')) as stream:return json.load(stream)


def read_text(path,errors=None):
    actual=resolve(path)
    if actual.suffix!='.gz':return actual.read_text(errors=errors)
    with gzip.open(actual,'rt',encoding='utf-8',errors=errors) as stream:return stream.read()


def copy_original(source,target):
    requested=Path(source);actual=resolve(requested)
    with (gzip.open(actual,'rb') if actual.suffix=='.gz' and requested.suffix!='.gz' else actual.open('rb')) as src,Path(target).open('wb') as dst:shutil.copyfileobj(src,dst)


def make_reference(path,target):
    """Remove only a verified duplicate, preserving transparent decoded reads."""
    path,target=Path(path).resolve(),Path(target).resolve();root=ROOT.resolve()
    if not path.is_relative_to(root) or not target.is_relative_to(root) or path==target or path.suffix!=target.suffix or path.suffix not in ('.fs','.json','.tr'):
        raise ValueError('Reference requires distinct workspace artifacts of one type')
    reference=path.with_name(path.name+'.ref.json')
    if reference.exists():raise FileExistsError(reference)
    actual=resolve(path)
    if actual.is_symlink() or not actual.resolve().is_relative_to(root):raise ValueError('Duplicate is outside workspace')
    if resolve(target).resolve()==actual.resolve():raise ValueError('Reference target depends on duplicate')
    original=sha(path)
    if sha(target)!=original:raise ValueError('Reference target content differs')
    row=dict(schema='nextgowin-artifact-reference-v1',target=target.relative_to(root).as_posix(),sha256=original)
    before=actual.stat().st_size
    with reference.open('x',encoding='utf-8') as stream:json.dump(row,stream,indent=2);stream.write('\n')
    if sha(path)!=original or sha(target)!=original:raise ValueError('Artifact changed during deduplication')
    actual.unlink()
    if sha(path)!=original:raise ValueError('Reference did not preserve original content')
    return dict(original=str(path.relative_to(root)),target=row['target'],original_sha256=original,removed_bytes=before,reference_bytes=reference.stat().st_size)


def compact(path):return _compact(path,'.fs')


def compact_json(path):return _compact(path,'.json')


def compact_text(path):return _compact(path,'.tr')


def _compact(path,suffix):
    path=Path(path).resolve()
    if path.suffix!=suffix or path.is_symlink() or path.stat().st_file_attributes&0x400:raise ValueError('Only regular supported artifacts may be compacted')
    target=path.with_name(path.name+'.gz')
    if target.exists():raise FileExistsError(target)
    original=sha(path);before=path.stat().st_size
    with path.open('rb') as source,gzip.open(target,'xb',compresslevel=6) as destination:shutil.copyfileobj(source,destination)
    with gzip.open(target,'rb') as stream:decoded=hashlib.file_digest(stream,'sha256').hexdigest()
    if decoded!=original or sha(path)!=original:raise ValueError('FS changed during lossless compression')
    if path.stat().st_file_attributes&1:path.chmod(stat.S_IWRITE)
    path.unlink()
    return dict(original_name=str(path),original_bytes=before,original_sha256=original,compressed_name=str(target),compressed_bytes=target.stat().st_size,compressed_sha256=sha(target))
