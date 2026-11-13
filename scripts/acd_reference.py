"""Small exact ACD reference for validation, not the full R1 mapping algorithm.

Enumerates bound/free/shared partitions, encodes distinct cofactor columns,
and reconstructs a two-level LUT network. Bit i of a truth table corresponds
to input assignment i, with variable zero as the least-significant bit.
"""
import argparse
from itertools import combinations
import json
from pathlib import Path


def project(word, variables):
    return sum(((word >> v) & 1) << i for i, v in enumerate(variables))


def scatter(value, variables):
    return sum(((value >> i) & 1) << v for i, v in enumerate(variables))


def decompose(truth, n, k, bound, shared=()):
    bound, shared = tuple(bound), tuple(shared)
    if set(bound) & set(shared) or not bound or len(bound)+len(shared)>k:
        return None
    free = tuple(v for v in range(n) if v not in bound and v not in shared)
    encodings = []
    width = 0
    for s in range(1 << len(shared)):
        signatures, codes = {}, []
        for b in range(1 << len(bound)):
            base = scatter(s, shared) | scatter(b, bound)
            signature = sum(((truth >> (base | scatter(f, free))) & 1) << f
                            for f in range(1 << len(free)))
            if signature not in signatures:
                signatures[signature] = len(signatures)
            codes.append(signatures[signature])
        width = max(width, (len(signatures)-1).bit_length())
        encodings.append((signatures, codes))
    if width+len(free)+len(shared)>k:
        return None
    leaves = [0]*width
    root = 0
    for s, (signatures, codes) in enumerate(encodings):
        for b, code in enumerate(codes):
            leaf_index = b | (s << len(bound))
            for bit in range(width):
                leaves[bit] |= ((code >> bit) & 1) << leaf_index
        for signature, code in signatures.items():
            for f in range(1 << len(free)):
                root_index = f | (s << len(free)) | (code << (len(free)+len(shared)))
                root |= ((signature >> f) & 1) << root_index
    return dict(n=n, k=k, bound=list(bound), shared=list(shared), free=list(free),
                width=width, leaves=leaves, root=root)


def evaluate(candidate, assignment):
    b = project(assignment, candidate['bound'])
    s = project(assignment, candidate['shared'])
    f = project(assignment, candidate['free'])
    leaf_index = b | (s << len(candidate['bound']))
    code = sum(((tt >> leaf_index) & 1) << i for i, tt in enumerate(candidate['leaves']))
    index = f | (s << len(candidate['free'])) | (code << (len(candidate['free'])+len(candidate['shared'])))
    return (candidate['root'] >> index) & 1


def search(truth, arrivals, k=4, lut_delay=1000):
    n = len(arrivals)
    if n>8 or n<=k or lut_delay<=0 or truth<0 or truth.bit_length()>(1<<n):
        raise ValueError('Reference search requires k < n <= 8 and a valid truth table/delay')
    output = []
    for size in range(1, min(k,n)+1):
        for bound in combinations(range(n), size):
            rest = [v for v in range(n) if v not in bound]
            for ss in range(min(k-size, len(rest))+1):
                for shared in combinations(rest, ss):
                    c = decompose(truth, n, k, bound, shared)
                    if c is None:
                        continue
                    # Verify the constructed functions independently by exhaustive evaluation.
                    if any(evaluate(c, x) != ((truth >> x)&1) for x in range(1<<n)):
                        raise AssertionError('ACD reconstruction mismatch')
                    inputs = [arrivals[v] for v in c['free']+c['shared']]
                    if c['width']:
                        inputs += [max(arrivals[v] for v in c['bound']+c['shared'])+lut_delay]*c['width']
                    c['arrival_ps'] = max(inputs, default=0)+lut_delay
                    c['lut_count'] = c['width']+1
                    output.append(c)
    return sorted(output, key=lambda c: (c['arrival_ps'],c['lut_count'],c['bound'],c['shared']))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', type=Path, required=True)
    ap.add_argument('--model', type=Path, help='Verified Gowin cell delay envelope; excludes routing')
    ap.add_argument('--required-ps', type=int, default=6000)
    args = ap.parse_args()
    model = json.loads(args.model.read_text()) if args.model else None
    if model and (model.get('schema') != 'gowin138-synthesis-envelope-v1' or model.get('validated_rows') != 317):
        raise ValueError('Unverified model schema/coverage')
    lut_delay = model['lut_envelope_ps'] if model else 1000
    truth = sum((((x & 31).bit_count()%2) & ((x>>5)&1)) << x for x in range(64))
    early = search(truth, [0]*6, lut_delay=lut_delay)
    late = search(truth, [0,0,0,0,0,5000], lut_delay=lut_delay)
    forced = [c for c in late if 5 in c['bound']]
    result = dict(scope='Exact small ACD reference; uniform LUT delay or Gowin cell envelope; no routing or full mapping',
        units='ps', truth_hex=hex(truth), input_arrivals_ps=[0,0,0,0,0,5000],
        lut_delay_ps=lut_delay, required_ps=args.required_ps,
        meets_required=late[0]['arrival_ps']<=args.required_ps,
        feasible_under_required=sum(c['arrival_ps']<=args.required_ps for c in late),
        model_database_sha256=model['database_sha256'] if model else None,
        early_best=early[0], late_best=late[0], late_in_bound_best=forced[0],
        late_variable_zero_best=search(truth, [5000,0,0,0,0,0],lut_delay=lut_delay)[0],
        feasible_candidates=len(late), exhaustive_equivalence=True)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=2)+'\n')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
