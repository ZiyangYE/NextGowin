"""Install hash-checked WIP artifacts; never overwrite different existing data."""
import argparse,hashlib,json,os,shutil,sys,urllib.request,zipfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from workspace_budget import reserve


def digest(path):
    with path.open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()


def safe(name):
    if ':' in name or name.startswith(('/','\\')):raise ValueError('Absolute archive path')
    path=(ROOT/name).resolve()
    if not path.is_relative_to(ROOT):raise ValueError('Archive path leaves checkout')
    for p in [path,*path.parents]:
        if p==ROOT.parent:break
        if p.exists() and (p.is_symlink() or getattr(p.stat(follow_symlinks=False),'st_file_attributes',0)&0x400):raise ValueError('Reparse path refused')
    return path


def main():
    p=argparse.ArgumentParser();p.add_argument('--archive',type=Path);p.add_argument('--keep-downloads',action='store_true');a=p.parse_args()
    manifest=json.loads((ROOT/'checkpoint/download.json').read_text());inventory=json.loads((ROOT/'checkpoint/artifacts.json').read_text())
    reserve(ROOT,2*manifest['archive_bytes']+manifest['unpacked_bytes']+10_000_000)
    cached=[]
    if a.archive:archive=a.archive.resolve()
    else:
        cache=safe('checkpoint/.downloads');cache.mkdir(exist_ok=True)
        for row in manifest['parts']:
            if '/' in row['name'] or '\\' in row['name'] or row['bytes']>8_000_000:raise ValueError('Invalid part')
            target=safe('checkpoint/.downloads/'+row['name'])
            if not target.exists():
                partial=target.with_name(target.name+'.partial')
                request=urllib.request.Request(manifest['release']+'/'+row['name'],headers={'User-Agent':'NextGowin-checkpoint-installer'})
                with urllib.request.urlopen(request,timeout=120) as src,partial.open('xb') as dst:shutil.copyfileobj(src,dst)
                if partial.stat().st_size!=row['bytes'] or digest(partial)!=row['sha256']:raise ValueError('Downloaded part mismatch')
                partial.rename(target)
            if target.stat().st_size!=row['bytes'] or digest(target)!=row['sha256']:raise ValueError('Cached part mismatch')
            cached.append(target);print('VERIFIED',row['name'],flush=True)
        archive=safe('checkpoint/.downloads/checkpoint.zip')
        if not archive.exists():
            with archive.open('xb') as dst:
                for part in cached:
                    with part.open('rb') as src:shutil.copyfileobj(src,dst)
        cached.append(archive)
    if archive.stat().st_size!=manifest['archive_bytes'] or digest(archive)!=manifest['archive_sha256']:raise ValueError('Merged archive mismatch')
    expected={r['path']:r for r in inventory['files']}
    with zipfile.ZipFile(archive) as z:
        if len(z.infolist())!=len(expected) or set(z.namelist())!=set(expected):raise ValueError('Archive inventory mismatch')
        if sum(i.file_size for i in z.infolist())!=manifest['unpacked_bytes']:raise ValueError('Archive size mismatch')
        for info in z.infolist():
            if (info.external_attr>>16)&0o170000==0o120000:raise ValueError('Archive symlink refused')
            target=safe(info.filename);row=expected[info.filename]
            if info.file_size!=row['bytes']:raise ValueError('Member size mismatch')
            if target.exists():
                if target.stat().st_size!=row['bytes'] or digest(target)!=row['sha256']:raise ValueError('Existing file differs: '+info.filename)
                continue
            target.parent.mkdir(parents=True,exist_ok=True)
            with z.open(info) as src,target.open('xb') as dst:shutil.copyfileobj(src,dst)
            if digest(target)!=row['sha256']:raise ValueError('Extracted member mismatch: '+info.filename)
    if not a.keep_downloads:
        for path in cached:
            if not path.resolve().is_relative_to(ROOT/'checkpoint/.downloads'):raise ValueError('Unsafe cache removal')
            path.unlink()
    print(json.dumps(dict(status='checkpoint_artifacts_installed',files=len(expected),bytes=manifest['unpacked_bytes'])))


if __name__=='__main__':main()
