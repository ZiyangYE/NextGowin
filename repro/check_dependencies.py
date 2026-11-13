"""Verify unpacked dependency bytes before running native code."""
import hashlib,json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
data=json.loads((ROOT/'repro/installed-dependencies.json').read_text())
for row in data['files']:
    path=(ROOT/row['path']).resolve()
    if not path.is_relative_to(ROOT) or not path.is_file() or path.is_symlink() or path.stat().st_size!=row['bytes']:raise ValueError('Missing/invalid dependency: '+row['path'])
    with path.open('rb') as stream:digest=hashlib.file_digest(stream,'sha256').hexdigest()
    if digest!=row['sha256']:raise ValueError('Dependency hash mismatch: '+row['path'])
print('DEPENDENCIES_VERIFIED',len(data['files']),'files;',data['raw_bytes'],'bytes')
