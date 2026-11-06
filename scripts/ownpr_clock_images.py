"""Lossless storage for clock calibration images, retaining original SHA256s."""
import gzip,hashlib,shutil,stat
from pathlib import Path


def image_path(folder,name):
    path=Path(folder)/name
    if path.is_file():return path
    compressed=path.with_name(path.name+'.gz')
    if compressed.is_file():return compressed
    raise FileNotFoundError(path)


def image_sha(path):
    path=Path(path)
    with (gzip.open(path,'rb') if path.suffix=='.gz' else path.open('rb')) as stream:
        return hashlib.file_digest(stream,'sha256').hexdigest()


def compact(folder):
    folder=Path(folder).resolve();names=('encoding_first.fs','encoding_baseline.fs','encoding_clock.fs','encoding_restored.fs','probe.fs','probe.bin','probe.binx')
    rows=[]
    for name in names:
        p=folder/name
        if not p.exists():continue
        if p.is_symlink() or p.resolve().parent!=folder or getattr(p.stat(follow_symlinks=False),'st_file_attributes',0)&0x400:raise ValueError('Clock image is a reparse path')
        target=p.with_name(p.name+'.gz')
        if target.exists():raise FileExistsError(target)
        rows.append(dict(name=name,original_bytes=p.stat().st_size,original_sha256=image_sha(p),compressed_name=target.name))
    for row in rows:
        p=folder/row['name'];target=folder/row['compressed_name']
        with p.open('rb') as source,gzip.open(target,'xb',compresslevel=6) as dest:shutil.copyfileobj(source,dest)
        row['compressed_bytes']=target.stat().st_size
        row['compressed_sha256']=hashlib.sha256(target.read_bytes()).hexdigest()
    # Verify every compressed stream and original before deleting any original.
    for row in rows:
        if image_sha(folder/row['name'])!=row['original_sha256'] or image_sha(folder/row['compressed_name'])!=row['original_sha256']:raise ValueError('Clock image changed during compression')
    for row in rows:
        p=folder/row['name']
        if getattr(p.stat(),'st_file_attributes',0)&1:p.chmod(stat.S_IWRITE)
        p.unlink()
    return dict(status='lossless_clock_images_compacted',files=rows,original_bytes=sum(r['original_bytes'] for r in rows),compressed_bytes=sum(r['compressed_bytes'] for r in rows))
