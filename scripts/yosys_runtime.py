"""Project-local native Yosys invocation with explicit source/log provenance."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time

ROOT = Path(__file__).resolve().parents[1]
SUITE = ROOT/'build/dependencies/oss-cad-suite'
YOSYS = SUITE/'bin/yosys.exe'


def quote(path):
    value = Path(path).resolve().as_posix()
    if any(c in value for c in '\r\n"'):
        raise ValueError('Unsupported Yosys path')
    return '"'+value+'"'


def run(commands, directory, name, timeout=1800):
    from flow_budget import clipped
    timeout=clipped(timeout)
    script = directory/(name+'.ys')
    script.write_text('\n'.join(commands)+'\n', encoding='utf-8')
    env = dict(os.environ)
    env['PATH'] = str(SUITE/'lib')+os.pathsep+str(SUITE/'bin')+os.pathsep+env.get('PATH', '')
    env['OMP_NUM_THREADS'] = '1'
    started = time.perf_counter()
    with (directory/(name+'.console.log')).open('w', encoding='utf-8') as log:
        cp = subprocess.run([str(YOSYS), '-Q', '-T', '-l', str(directory/(name+'.log')), '-s', str(script)],
            cwd=directory, env=env, stdout=log, stderr=subprocess.STDOUT, timeout=timeout)
    result = dict(returncode=cp.returncode, wall_seconds=time.perf_counter()-started,
        binary_sha256=hashlib.sha256(YOSYS.read_bytes()).hexdigest(),
        script_sha256=hashlib.sha256(script.read_bytes()).hexdigest(), log=name+'.log')
    (directory/(name+'.invocation.json')).write_text(json.dumps(result, indent=2)+'\n')
    if cp.returncode:
        raise RuntimeError('Yosys failed: '+str(directory/(name+'.console.log')))
    return result
