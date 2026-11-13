"""Compare unmodified ABC, CPU fallback hooks, and GPU-produced exact tables."""
import argparse
import json
import os
from pathlib import Path
import statistics
import subprocess
import time
from run_abc_reproduction import ROOT,ABC_SOURCE,cec,sha,network_sha,stats


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('input',type=Path)
    ap.add_argument('--reference',type=Path,required=True)
    ap.add_argument('--partitions',type=Path,required=True)
    ap.add_argument('--lut',type=int,choices=[4,6],default=6)
    ap.add_argument('--repeats',type=int,default=5)
    ap.add_argument('--out',type=Path,required=True)
    args=ap.parse_args()
    out=args.out.resolve();out.mkdir(parents=True,exist_ok=False)
    gpu=ROOT/'build/cuda/acd_partition_bench.exe'
    patched=ROOT/'build/abc_mu/_TEST/abc.exe';ordinary=ROOT/'build/abc/_TEST/abc.exe'
    cache=out/'mu_cache.bin'
    result=dict(scope='Real ABC mapper using GPU-precomputed multiplicities; misses use original CPU evaluation',
        input_sha256=sha(args.input),partition_sha256=sha(args.partitions),
        ordinary_binary_sha256=sha(ordinary),patched_binary_sha256=sha(patched),runs={})
    def save():
        (out/'manifest.json').write_text(json.dumps(result,indent=2)+'\n')
    started=time.perf_counter()
    validation=subprocess.run([str(gpu),str(args.partitions.resolve()),str(cache)],capture_output=True,text=True,check=True,timeout=120)
    result['table_validation']=json.loads(validation.stdout)
    result['table_validation_wall_seconds']=time.perf_counter()-started
    if not result['table_validation']['cpu_validated']:
        raise RuntimeError('GPU table not CPU validated')
    verified_hash=sha(cache)
    started=time.perf_counter()
    producer=subprocess.run([str(gpu),str(args.partitions.resolve()),str(cache),'--produce'],capture_output=True,text=True,check=True,timeout=120)
    result['producer']=json.loads(producer.stdout)
    result['producer_wall_seconds']=time.perf_counter()-started
    if sha(cache)!=verified_hash:
        raise RuntimeError('Production table differs from CPU-validated table')
    result['cache_sha256']=verified_hash;save()
    environment=dict(os.environ)
    for key in ['NEXTGOWIN_MU_CACHE','NEXTGOWIN_MU_STATS','NEXTGOWIN_MU_VERIFY']:
        environment.pop(key,None)
    proofs={};expected_digest=None
    modes=['ordinary','fallback','cached','verified']
    for rep in range(args.repeats):
        for mode in modes:
            if mode=='verified' and rep>0:
                continue
            folder=out/mode;folder.mkdir(exist_ok=True)
            mapped=folder/f'mapped_{rep}.blif';statistics_file=folder/f'stats_{rep}.json'
            env=dict(environment)
            if mode!='ordinary':
                env['NEXTGOWIN_MU_STATS']=str(statistics_file)
            if mode in ['cached','verified']:
                env['NEXTGOWIN_MU_CACHE']=str(cache)
            if mode=='verified':
                env['NEXTGOWIN_MU_VERIFY']='1'
            binary=ordinary if mode=='ordinary' else patched
            command=f'read "{args.input.resolve().as_posix()}"; time -c; dch; if -Z {args.lut} -K {args.lut+2}; time -p; print_stats; write_blif "{mapped.as_posix()}"; echo NEXTGOWIN_DONE'
            start=time.perf_counter()
            cp=subprocess.run([str(binary),'-c',command],cwd=ABC_SOURCE,env=env,capture_output=True,text=True,timeout=180)
            elapsed=time.perf_counter()-start
            (folder/f'run_{rep}.log').write_text(cp.stdout+cp.stderr)
            if cp.returncode or 'NEXTGOWIN_DONE' not in [s.strip() for s in cp.stdout.splitlines()]:
                raise RuntimeError(f'{mode} mapping failed; see log')
            row=dict(wall_seconds=elapsed,**stats(cp.stdout),network_sha256=network_sha(mapped),artifact_sha256=sha(mapped))
            if mode!='ordinary':
                row['cache_statistics']=json.loads(statistics_file.read_text())
            digest=row['network_sha256']
            if expected_digest is None:
                expected_digest=digest
            if digest!=expected_digest:
                raise RuntimeError('Patched/fallback mapping changed the selected network')
            if digest not in proofs:
                proofs[digest]=cec(ordinary,args.reference.resolve(),mapped,folder/f'cec_{rep}.log',180)
            row['cec']=proofs[digest]
            if not row['cec']['equivalent']:
                raise RuntimeError('Output equivalence unproven')
            if mode=='verified':
                s=row['cache_statistics']
                if s['hits']==0 or s['hits']!=s['verified']:
                    raise RuntimeError('GPU table did not participate or was not verified')
            result['runs'].setdefault(mode,[]).append(row);save()
    result['summary']={mode:{metric:statistics.median(r[metric] for r in rows)
        for metric in ['wall_seconds','mapping_cpu_seconds']} for mode,rows in result['runs'].items()}
    result['cold_single_compile_cached_seconds']=result['producer_wall_seconds']+result['summary']['cached']['wall_seconds']
    result['cold_cost_exclusions']='Corpus acquisition and Python partition preparation are additional; warm cache timings assume reuse'
    result['status']='complete';save();print(json.dumps(result['summary'],indent=2))


if __name__=='__main__':
    main()
