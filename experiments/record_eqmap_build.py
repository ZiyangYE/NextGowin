"""Retain exact upstream, compiler, lockfile and executable identities."""
import hashlib
import json
from pathlib import Path
import subprocess
ROOT=Path(__file__).resolve().parents[1]
source=ROOT/'third_party/eqmap'
compiler=ROOT/'build/eqmap_toolchain/rustup/toolchains/stable-x86_64-pc-windows-msvc/bin/rustc.exe'
binary=ROOT/'build/eqmap_target/release/eqmap_fpga.exe'
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
git=lambda *args:subprocess.check_output(['git','-c','safe.directory='+source.as_posix(),'-C',str(source),*args],text=True).strip()
r=dict(upstream='https://github.com/cornell-zhang/eqmap',commit=git('rev-parse','HEAD'),
       source_worktree_status=git('status','--porcelain'),license='Apache-2.0',
       cargo_lock_sha256=sha(source/'Cargo.lock'),binary_sha256=sha(binary),compiler_sha256=sha(compiler),
       compiler_version=subprocess.check_output([str(compiler),'-vV'],text=True),
       build_command='cargo build --release --locked --bin eqmap_fpga --manifest-path third_party/eqmap/Cargo.toml -j 2',
       runtime_search='Serial default EqMap greedy extraction; no optional ILP feature or GPU; fixed per-cut limits in adapter.',
       toolchain_scope='Workspace-local CARGO_HOME/RUSTUP_HOME and target directory; PATH unchanged.')
(ROOT/'results/eqmap_build_001_metadata.json').write_text(json.dumps(r,indent=2)+'\n')
print(r)
