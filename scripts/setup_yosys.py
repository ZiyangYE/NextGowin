"""Extract a hash-pinned, project-local Yosys runtime from the official suite."""
import argparse
import hashlib
import json
from pathlib import Path
import tarfile
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
NAME = 'oss-cad-suite-windows-x64-20261031.tgz'
URL = 'https://github.com/YosysHQ/oss-cad-suite-build/releases/download/2026-10-31/' + NAME
DIGEST = '4e845291d99e1711e4c3a005661132deef8f8cc0afaf1e3fd0f4811cc103e783'


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--download', action='store_true')
    args = ap.parse_args()
    base = ROOT/'build/dependencies'
    base.mkdir(parents=True, exist_ok=True)
    archive = base/NAME
    if not archive.exists():
        if not args.download:
            raise FileNotFoundError('Download the official archive first, or use --download')
        urllib.request.urlretrieve(URL, archive)
    with archive.open('rb') as stream:
        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
    if digest != DIGEST:
        raise ValueError('Official archive SHA256 mismatch')
    selected = []
    executables = {'yosys.exe', 'yosys-abc.exe', 'iverilog.exe', 'vvp.exe', 'z3.exe', 'libvvp-1.dll','libfst-1.dll'}
    with tarfile.open(archive) as tar:
        for member in tar:
            parts = Path(member.name).parts
            if not member.isfile() or len(parts) < 3 or parts[0] != 'oss-cad-suite':
                continue
            if '..' in parts or Path(member.name).is_absolute():
                raise ValueError('Invalid archive member path')
            include = (parts[1] == 'bin' and parts[2] in executables
                       or parts[1:3] == ('share', 'yosys')
                       or parts[1:3] == ('lib', 'ivl')
                       or parts[1] == 'lib' and len(parts) == 3 and parts[2].endswith('.dll')
                       or parts[1] == 'license')
            if not include:
                continue
            target = base.joinpath(*parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            data = tar.extractfile(member).read()
            target.write_bytes(data)
            selected.append(dict(path='/'.join(parts), sha256=hashlib.sha256(data).hexdigest()))
    (base/'yosys_runtime.json').write_text(json.dumps(dict(
        url=URL, archive_sha256=DIGEST, files=selected), indent=2)+'\n')
    print(f'Extracted {len(selected)} runtime/library/license files into {base}')


if __name__ == '__main__':
    main()
