"""Capture initialized STA tables and deliberately stop before placement.

Internal addresses are accepted only for the audited DLL hash.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from gowin_probe import BIN, ROOT, prepare
from build_timing_model import read_snapshot

EXPECTED = 'bc9472a5a22cf4a94e39e07439f261f3354c668f92ef9abc735e6678bea53b7b'


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--out',type=Path,required=True)
    args = ap.parse_args()
    dll_hash = hashlib.sha256((BIN/'gowin.dll').read_bytes()).hexdigest()
    if dll_hash!=EXPECTED:
        raise RuntimeError('Different DLL build: re-audit addresses before capture')
    out = args.out.resolve()
    prepare(out)
    cdb = Path('C:/Program Files (x86)/Windows Kits/10/Debuggers/x64/cdb.exe')
    python = ROOT/'binary_audit/flow/runtime/bin/python.exe'
    commands = ROOT/'binary_audit/timing/capture_rows.cdb'
    log = out/'snapshot.log'
    command = [str(cdb),'-G','-logo',str(log),'-cf',str(commands),str(python),
        str(ROOT/'scripts/gowin_probe.py'),'--worker','pnr','--out',str(out)]
    started = time.time()
    cp = subprocess.run(command,cwd=python.parent,env=dict(os.environ,PYTHONHOME=sys.base_prefix),
        stdout=subprocess.DEVNULL,stderr=subprocess.STDOUT,timeout=60)
    metadata, rows = read_snapshot(log)
    if cp.returncode or len(rows)!=317:
        raise RuntimeError('Capture failed or incomplete')
    manifest = dict(scope='Runtime library capture only; debugger exits before placement. Not a completed P&R run.',
        dll_sha256=dll_hash,commands_sha256=hashlib.sha256(commands.read_bytes()).hexdigest(),
        input_sha256=hashlib.sha256((out/'probe.vg').read_bytes()).hexdigest(),
        snapshot_sha256=hashlib.sha256(log.read_bytes()).hexdigest(),
        debugger_returncode=cp.returncode,metadata=metadata,rows=len(rows),elapsed_seconds=time.time()-started)
    (out/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    print(json.dumps(manifest,indent=2))


if __name__=='__main__':
    main()
