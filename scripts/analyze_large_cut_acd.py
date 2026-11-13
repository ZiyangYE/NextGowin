"""Exact bounded functional decompositions; no physical timing claims."""
import argparse
import itertools
import json
import time
from pathlib import Path
from acd_reference import decompose, evaluate
from ownpr_image_storage import load_json, sha


def candidates(truth,max_shared=1,max_encoder=5):
    if max_shared not in (0,1,2,3):raise ValueError('Shared-variable bound must be 0..3')
    if not 4<=max_encoder<=7:raise ValueError('Encoder support bound must be 4..7')
    from register_control_map import normalize
    support,reduced=normalize(list(range(8)),truth)
    if len(support)<=7:
        direct=dict(n=8,k=7,bound=[],shared=[],free=list(support),width=0,leaves=[],root=reduced,
                    encoder_support=0,root_support=len(support),lut4_slots_upper_bound=1<<max(0,len(support)-4),direct_function=True)
        if any(evaluate(direct,a)!=((truth>>a)&1) for a in range(256)):raise ValueError('Direct support reduction failed')
        return 1,[direct]
    found=[]
    # Encoder support <=5, at most one shared variable, root support <=7.
    # These bounds define a search family, not functional minimality.
    for count in range(2,max_encoder+1):
        for bound in itertools.combinations(range(8),count):
            remaining=[i for i in range(8) if i not in bound]
            choices=[s for size in range(min(max_shared,max_encoder-count)+1) for s in itertools.combinations(remaining,size)]
            for shared in choices:
                c=decompose(truth,8,7,bound,shared)
                if c is None:continue
                lw=len(bound)+len(shared);rw=len(c['free'])+len(shared)+c['width']
                slots=c['width']*(1<<max(0,lw-4))+(1<<max(0,rw-4))
                found.append(dict(c,encoder_support=lw,root_support=rw,lut4_slots_upper_bound=slots))
    found.sort(key=lambda c:(c['lut4_slots_upper_bound'],c['encoder_support'],c['root_support'],c['bound'],c['shared']))
    best=found[:3]
    for c in best:
        if any(evaluate(c,a)!=((truth>>a)&1) for a in range(256)):raise ValueError('Reconstruction failed')
    return len(found),best


def main():
    p=argparse.ArgumentParser();p.add_argument('--inventory',type=Path,required=True);p.add_argument('--out',type=Path,required=True);a=p.parse_args()
    from workspace_budget import reserve
    reserve(Path(__file__).resolve().parents[1],2_000_000)
    start=time.time();rows=[]
    for case in load_json(a.inventory)['designs']:
        for row in case['candidates']:
            count,best=candidates(int(row['truth_hex'],16))
            rows.append(dict(design=case['design'],root=row['root'],feasible_count=count,best=best))
    result=dict(scope=__doc__,inventory_sha256=sha(a.inventory),implementation_sha256=sha(Path(__file__)),reference_sha256=sha(Path(__file__).with_name('acd_reference.py')),rows=rows,wall_seconds=time.time()-start,physical_candidate_created=False)
    with a.out.open('x') as f:json.dump(result,f,indent=2)
    print(json.dumps(dict(roots=len(rows),wall_seconds=result['wall_seconds'])))


if __name__=='__main__':main()
