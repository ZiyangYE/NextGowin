"""Verify the small source distribution without loading tools or device data."""
import hashlib,json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
manifest=json.loads((ROOT/'source_manifest.json').read_text(encoding='utf-8'))
total=0
for row in manifest['files']:
    p=(ROOT/row['path']).resolve()
    if not p.is_relative_to(ROOT) or p.is_symlink():raise ValueError('Invalid package member')
    data=p.read_bytes()
    if len(data)!=row['bytes'] or hashlib.sha256(data).hexdigest()!=row['sha256']:raise ValueError('Changed package member: '+row['path'])
    if p.suffix=='.py':compile(data,str(p),'exec')
    total+=len(data)
print(f'Verified {len(manifest["files"])} files, {total} bytes; Python syntax checked. No device or timing validation performed.')
