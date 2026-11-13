"""Capture actual acd_evaluate calls from an ABC mapper run."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import struct
import subprocess
import time
from run_abc_reproduction import ROOT,ABC_SOURCE,cec,stats,sha


def records(path):
    data=path.read_bytes()
    if data[:8]!=b'ACDCUT01':
        raise ValueError('Bad ACD corpus magic')
    pos=8
    while pos<len(data):
        if pos+28>len(data):
            raise ValueError('Truncated ACD record header')
        n,k,late,flag,profile,cost,status=struct.unpack_from('<6Ii',data,pos);pos+=28
        if not 1<=n<=11 or not 3<=k<=6 or late>=(1<<n):
            raise ValueError('Invalid ACD record dimensions')
        size=8*(1<<max(0,n-6))
        if pos+size>len(data):
            raise ValueError('Truncated truth table')
        truth=list(struct.unpack_from('<'+str(size//8)+'Q',data,pos));pos+=size
        yield dict(n=n,k=k,late_mask=late,try_without_late=flag,profile=profile,cost=cost,status=status,truth=truth)


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('input',type=Path,help='Preprocessed AIG shared with baseline experiment')
    ap.add_argument('--reference',type=Path,required=True)
    ap.add_argument('--out',type=Path,required=True)
    ap.add_argument('--lut',type=int,choices=[4,6],default=6)
    args=ap.parse_args()
    out=args.out.resolve();out.mkdir(parents=True,exist_ok=False)
    corpus=out/'cuts.bin';mapped=out/'mapped.blif'
    binary=ROOT/'build/abc_profile/_TEST/abc.exe'
    command=f'read "{args.input.resolve().as_posix()}"; time -c; dch; if -Z {args.lut} -K {args.lut+2}; time -p; print_stats; write_blif "{mapped.as_posix()}"; echo NEXTGOWIN_DONE'
    start=time.perf_counter()
    cp=subprocess.run([str(binary),'-c',command],cwd=ABC_SOURCE,
        env=dict(os.environ,NEXTGOWIN_ACD_CORPUS=str(corpus)),capture_output=True,text=True,timeout=180)
    (out/'mapping.log').write_text(cp.stdout+cp.stderr)
    if cp.returncode or 'NEXTGOWIN_DONE' not in [s.strip() for s in cp.stdout.splitlines()]:
        raise RuntimeError('Instrumented mapping failed')
    data=list(records(corpus))
    profile=json.loads(Path(str(corpus)+'.json').read_text())
    if len(data)!=profile['saved'] or not data:
        raise RuntimeError('Corpus missing or count mismatch; instrumentation may not have linked')
    verify=cec(ROOT/'build/abc/_TEST/abc.exe',args.reference.resolve(),mapped,out/'cec.log',180)
    result=dict(scope='Actual mapper ACD calls; bounded first 20000 records, no representative sampling claim',
        input_sha256=sha(args.input),corpus_sha256=sha(corpus),profiler_binary_sha256=sha(binary),
        command=command,mapping=stats(cp.stdout),profile=profile,
        records=len(data),dimensions=sorted({(r['n'],r['k']) for r in data}),
        wall_seconds=time.perf_counter()-start,cec=verify)
    (out/'manifest.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result,indent=2))
    if not verify['equivalent']:
        raise RuntimeError('Instrumented output equivalence unproven')


if __name__=='__main__':
    main()
