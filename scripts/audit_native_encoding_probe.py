"""Reject native encoder differential experiments without a reversible signal.

Bitstream comments (including creation time) are excluded. No bitstream format
or device encoding is inferred by this audit: it only establishes whether the
calibration could support such inference at all.
"""
import argparse, gzip, hashlib, json
from pathlib import Path


def payload(path):
    from ownpr_image_storage import resolve
    path=resolve(path)
    if path.suffix=='.gz':
        with gzip.open(path,'rt',encoding='ascii') as stream:lines=stream.read().splitlines()
    else:lines=path.read_text().splitlines()
    bits=[line.strip() for line in lines if line.strip() and not line.startswith('//')]
    if not bits or any(set(line)-{'0','1'} for line in bits):
        raise ValueError('Expected uncompressed native ASCII bitstream')
    return bits


def difference(a,b):
    if len(a)!=len(b) or any(len(x)!=len(y) for x,y in zip(a,b)):
        return dict(same_shape=False,changed_lines=None,changed_bits=None)
    return dict(same_shape=True,changed_lines=sum(x!=y for x,y in zip(a,b)),
                changed_bits=sum(sum(u!=v for u,v in zip(x,y)) for x,y in zip(a,b)))


def audit(folder):
    folder=Path(folder);labels=('baseline','variant','restored')
    data={k:payload(folder/f'encoding_{k}.fs') for k in labels}
    comparison={f'{a}_{b}':difference(data[a],data[b]) for a,b in
                [('baseline','variant'),('baseline','restored'),('variant','restored')]}
    reasons=[]
    if data['baseline']!=data['restored']:reasons.append('baseline_not_restored')
    if data['variant']==data['restored']:reasons.append('mutation_not_observable')
    if any(not x['same_shape'] for x in comparison.values()):reasons.append('payload_shape_changed')
    return dict(status='rejected' if reasons else 'reversible_difference',reasons=reasons,
                comparisons=comparison,payload_sha256={k:hashlib.sha256('\n'.join(v).encode()).hexdigest() for k,v in data.items()},
                configuration_map_verified=False,usable_for_encoding_inference=not reasons,
                scope='Differential precondition only. A reversible difference does not identify configuration bits or prove hardware functionality.')


if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('folder',type=Path);args=ap.parse_args()
    result=audit(args.folder);(args.folder/'encoding_audit.json').write_text(json.dumps(result,indent=2));print(json.dumps(result,indent=2))
