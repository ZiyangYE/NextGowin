"""Paired, bounded FAPO idea reproduction; not paper-equivalent heuristic code."""
import argparse
import json
from pathlib import Path
import time
from fapo_reference import seed,clean,timing,optimize,write_blif
from gowin_blif import read_blif
from run_abc_reproduction import ROOT,cec,sha


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--parents',type=Path,required=True)
    ap.add_argument('--lut',type=int,choices=[4,6],default=4)
    ap.add_argument('--benchmarks',nargs='+',default=['ctrl','priority','router'])
    ap.add_argument('--sweeps',type=int,default=4)
    ap.add_argument('--max-roots',type=int,default=12)
    ap.add_argument('--out',type=Path,required=True)
    args=ap.parse_args();out=args.out.resolve();out.mkdir(parents=True,exist_ok=False)
    result=dict(scope='Independent bounded implementation, small paired suite; no numerical reproduction of paper tables',
        parameters=dict(lut=args.lut,sweeps=args.sweeps,max_roots=args.max_roots,proposal_limit=128),
        optimizer_source_sha256=sha(ROOT/'scripts/fapo_reference.py'),
        abc_binary_sha256=sha(ROOT/'build/abc/_TEST/abc.exe'),runs=[],status='running')
    settings=[('hybrid',1,.05,True),('greedy',1,.05,False),('zero_area',1,0,True),('depth_only',0,.05,True)]
    for bench in args.benchmarks:
        for parent in ['baseline','acd_remap']:
            source=(args.parents/bench/(parent+'_0.blif')).resolve()
            reference=(args.parents/bench/'normalized.aig').resolve()
            initial=seed(read_blif(source,max_inputs=args.lut))
            for name,alpha,beta,cut in settings:
                folder=out/bench/parent/name;folder.mkdir(parents=True)
                started=time.perf_counter()
                selected,metrics=optimize(initial,args.lut,alpha,beta,args.sweeps,args.max_roots,cut_enabled=cut)
                elapsed=time.perf_counter()-started
                target=folder/'optimized.blif';target.write_text(write_blif(selected))
                serialized=clean(seed(read_blif(target,max_inputs=args.lut)),args.lut)
                actual=timing(serialized,alpha)
                if any(actual[k]!=metrics['selected'][k] for k in ['area','delay','atp']):
                    raise RuntimeError('Serialized output changed live LUT metrics')
                proof=cec(ROOT/'build/abc/_TEST/abc.exe',reference,target,folder/'cec.log',180)
                row=dict(benchmark=bench,parent=parent,strategy=name,alpha=alpha,beta=beta,cut_enabled=cut,
                    input_sha256=sha(source),reference_sha256=sha(reference),output_sha256=sha(target),
                    optimization_wall_seconds=elapsed,**metrics,cec=proof)
                (folder/'manifest.json').write_text(json.dumps(row,indent=2)+'\n')
                result['runs'].append(row)
                (out/'manifest.json').write_text(json.dumps(result,indent=2)+'\n')
                if not proof['equivalent']:raise RuntimeError('Equivalence unproven')
                print(f'{bench} {parent} {name}: {metrics["seed"]} -> {metrics["selected"]}',flush=True)
    result['status']='complete'
    (out/'manifest.json').write_text(json.dumps(result,indent=2)+'\n')


if __name__=='__main__':main()
