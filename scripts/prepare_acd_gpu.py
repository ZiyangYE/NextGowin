"""Expand captured ACD calls into late-mask-respecting partition tasks."""
import argparse
import hashlib
from itertools import combinations
import json
from pathlib import Path
import struct
from collect_acd_corpus import records


def scatter(value,variables):
    return sum(((value>>i)&1)<<v for i,v in enumerate(variables))


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('corpus',type=Path)
    ap.add_argument('--out',type=Path,required=True)
    ap.add_argument('--all-partitions',action='store_true',help='Deduplicate truths and precompute all free sets for mapper reuse')
    args=ap.parse_args()
    cuts=list(records(args.corpus))
    captured=len(cuts)
    if args.all_partitions:
        unique={}
        for cut in cuts:
            unique.setdefault((cut['n'],cut['k'],tuple(cut['truth'])),cut)
        cuts=list(unique.values())
    patterns=[];index={};tasks=[]
    for ci,cut in enumerate(cuts):
        n,k,late=cut['n'],cut['k'],cut['late_mask']
        if args.all_partitions:
            late=0
        for free_count in range(max(1,n-k,late.bit_count()),min(k-1,max(4,n-k))+1):
            for free in combinations(range(n),free_count):
                mask=sum(1<<v for v in free)
                if mask&late != late:
                    continue
                key=(n,k,mask)
                if key not in index:
                    bound=[v for v in range(n) if v not in free]
                    fs=[scatter(x,free) for x in range(1<<free_count)]
                    bs=[scatter(x,bound) for x in range(1<<len(bound))]
                    if len(fs)>64 or len(bs)>64:
                        raise ValueError('Partition exceeds 64-lane prototype')
                    index[key]=len(patterns)
                    patterns.append(dict(free_size=len(fs),bound_size=len(bs),free_count=free_count,k=k,
                        free_assignment=fs+[0]*(64-len(fs)),bound_assignment=bs+[0]*(64-len(bs))))
                tasks.append((ci,index[key]))
    if not tasks:
        raise ValueError('No valid partition tasks')
    args.out.mkdir(parents=True,exist_ok=False)
    blob=args.out/'partitions.bin'
    with blob.open('wb') as f:
        f.write(b'ACDPART1');f.write(struct.pack('<3I',len(cuts),len(patterns),len(tasks)))
        for cut in cuts:
            truth=cut['truth']+[0]*(32-len(cut['truth']))
            f.write(struct.pack('<II32Q',cut['n'],cut['k'],*truth))
        for p in patterns:
            f.write(struct.pack('<132I',p['free_size'],p['bound_size'],p['free_count'],p['k'],*p['free_assignment'],*p['bound_assignment']))
        for task in tasks:
            f.write(struct.pack('<2I',*task))
    manifest=dict(scope=('All free-set partitions for deduplicated truths; late constraints are applied by the consuming mapper' if args.all_partitions else
        'Partitions respecting each captured late mask; not full ACD decisions or encoding'),
        corpus_sha256=hashlib.sha256(args.corpus.read_bytes()).hexdigest(),
        task_blob_sha256=hashlib.sha256(blob.read_bytes()).hexdigest(),
        cuts=len(cuts),patterns=len(patterns),tasks=len(tasks),bytes=blob.stat().st_size,
        captured_calls=captured,all_partitions=args.all_partitions,
        policy='Support-reducing partitions; free-set bound follows pinned upstream defaults; no early stopping or cost-based pruning')
    (args.out/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    print(json.dumps(manifest,indent=2))


if __name__=='__main__':
    main()
