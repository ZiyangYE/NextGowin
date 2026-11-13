"""Joint cofactor-class encoding for several functions on one common support."""
from acd_reference import scatter, evaluate
from itertools import combinations


def decompose_many(truths,n,k,bound,shared=()):
    bound,shared=tuple(bound),tuple(shared)
    if not truths or not bound or set(bound)&set(shared) or len(bound)+len(shared)>k:return None
    if len(set(bound))!=len(bound) or len(set(shared))!=len(shared) or any(v<0 or v>=n for v in bound+shared):raise ValueError('Invalid partition')
    if any(t<0 or t.bit_length()>(1<<n) for t in truths):raise ValueError('Invalid truth table')
    free=tuple(v for v in range(n) if v not in bound and v not in shared);encodings=[];width=0
    for s in range(1<<len(shared)):
        signatures={};codes=[]
        for b in range(1<<len(bound)):
            base=scatter(s,shared)|scatter(b,bound)
            signature=tuple(sum(((truth>>(base|scatter(f,free)))&1)<<f for f in range(1<<len(free))) for truth in truths)
            if signature not in signatures:signatures[signature]=len(signatures)
            codes.append(signatures[signature])
        width=max(width,(len(signatures)-1).bit_length());encodings.append((signatures,codes))
    if width+len(free)+len(shared)>k:return None
    leaves=[0]*width;roots=[0]*len(truths)
    for s,(signatures,codes) in enumerate(encodings):
        for b,code in enumerate(codes):
            index=b|(s<<len(bound))
            for i in range(width):leaves[i]|=((code>>i)&1)<<index
        for signatures_per_output,code in signatures.items():
            for f in range(1<<len(free)):
                index=f|(s<<len(free))|(code<<(len(free)+len(shared)))
                for i,signature in enumerate(signatures_per_output):roots[i]|=((signature>>f)&1)<<index
    common=dict(n=n,k=k,bound=list(bound),shared=list(shared),free=list(free),width=width,leaves=leaves)
    return [dict(common,root=root) for root in roots]


def verify_many(candidates,truths):
    if len(candidates)!=len(truths):raise ValueError('Output count mismatch')
    for c,truth in zip(candidates,truths):
        if any(evaluate(c,a)!=((truth>>a)&1) for a in range(1<<c['n'])):raise ValueError('Multi-output ACD reconstruction failed')


def joint_choices(rows,rank=0):
    """Bounded greedy grouping based on shared physical LUT-slot cost.

    At most four roots per group. Physical timing must decide acceptance.
    """
    from ownpr_shared_encoder import canonical
    selected={r['root']:r['acd_options'][rank%len(r['acd_options'])] for r in rows}
    def cost(cs):
        encoders={};roots=0
        for c in cs:
            for word in c['leaves']:
                bits,word=canonical(c['bound']+c['shared'],word)
                if bits:
                    mask=(1<<(1<<len(bits)))-1;encoders[bits,min(word,mask^word)]=1<<max(0,len(bits)-4)
            bits,_=canonical(c['free']+c['shared']+list(range(8,8+c['width'])),c['root'])
            roots+=1<<max(0,len(bits)-4)
        return sum(encoders.values())+roots
    candidates=[];examined=0
    for count in range(2,min(4,len(rows))+1):
        for group in combinations(rows,count):
            if any(r['cut_bits']!=group[0]['cut_bits'] for r in group):continue
            # Evaluate common retained partitions, not every partition for every
            # subset of roots. This deterministic bound keeps proposals bounded.
            partitions=set.intersection(*[{(tuple(c['bound']),tuple(c['shared'])) for c in r['acd_options'] if c['bound']} for r in group])
            if not partitions or examined>=256:continue
            examined+=1
            names=[r['root'] for r in group];truths=[int(r['truth_hex'],16) for r in group];oldcost=cost([selected[n] for n in names]);best=None
            for bound,shared in sorted(partitions):
                cs=decompose_many(truths,8,7,bound,shared)
                if cs is None:continue
                newcost=cost(cs)
                if newcost>=oldcost:continue
                key=(newcost,bound,shared)
                if best is None or key<best[0]:best=(key,cs)
            if best:
                verify_many(best[1],truths);candidates.append(dict(roots=names,before_slots=oldcost,after_slots=best[0][0],saving=oldcost-best[0][0],certificates=best[1]))
    used=set();groups=[]
    for candidate in sorted(candidates,key=lambda c:(-c['saving'],-len(c['roots']),c['roots'])):
        if used.intersection(candidate['roots']):continue
        used.update(candidate['roots']);groups.append(candidate)
        selected.update(zip(candidate['roots'],candidate['certificates']))
    for g in groups:g['search_scope']='At most 256 root groups of size 2..4; only partitions common to each root retained top-three options.'
    return selected,groups
