"""Choose among verified mapped networks using original-constraint model slack."""
import argparse
import hashlib
import json
import math
from pathlib import Path
import shutil
from constrained_mapping import run
from gowin_blif import read_blif
from run_abc_reproduction import ROOT


def select(candidates,area_allowance=None):
    if area_allowance is not None and (not math.isfinite(area_allowance) or area_allowance<0):
        raise ValueError('Area allowance must be finite and nonnegative')
    baseline=candidates['baseline']['luts']
    cap=None if area_allowance is None else baseline+math.floor(baseline*area_allowance)
    eligible=[name for name,c in candidates.items() if cap is None or c['luts']<=cap]
    chosen=min(eligible,key=lambda name:(-candidates[name]['timing']['wns_ps'],
        -candidates[name]['timing']['tns_ps'],candidates[name]['luts'],name))
    return chosen,cap


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('input',type=Path)
    ap.add_argument('--constraints',type=Path,required=True)
    ap.add_argument('--model',type=Path,default=ROOT/'results/timing/gowin138_model.json')
    ap.add_argument('--area-allowance',type=float)
    ap.add_argument('--out',type=Path,required=True)
    args=ap.parse_args()
    out=args.out.resolve();out.mkdir(parents=True,exist_ok=False)
    candidates={}
    for strategy in ['baseline','acd','acd_remap']:
        result=run(args.input,args.constraints,args.model,out/strategy,strategy)
        logic=out/strategy/'mapped_logic.blif'
        network=read_blif(logic)
        candidates[strategy]=dict(timing=result['independent_timing'],
            luts=sum(bool(n['inputs']) for n in network['nodes']),
            cec_equivalent=result['cec']['equivalent'],annotations_preserved=result['annotations_preserved'],
            manifest=str(out/strategy/'manifest.json'),logic_sha256=hashlib.sha256(logic.read_bytes()).hexdigest())
    chosen,cap=select(candidates,args.area_allowance)
    for filename in ['mapped.blif','mapped_logic.blif']:
        shutil.copyfile(out/chosen/filename,out/filename)
    result=dict(schema='timing-portfolio-v1',scope='Verified combinational model timing; physical timing still needs P&R',
        policy='Maximize original-constraint WNS, then TNS, then minimize LUT count; baseline always eligible',
        chosen=chosen,area_cap=cap,candidates=candidates,
        status='satisfied' if candidates[chosen]['timing']['meets_required'] else 'violated')
    (out/'manifest.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result,indent=2))
    if result['status']=='violated':
        raise SystemExit(2)


if __name__=='__main__':
    main()
