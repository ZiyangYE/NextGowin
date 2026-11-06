"""Build a constrained synthesis model from hash-pinned STA runtime tables.

Physical pin permutations and every FF constraint are intentionally not inferred
from numerical coincidence. The first consumer uses a LUT delay envelope.
"""
import argparse
import hashlib
import json
import math
from pathlib import Path
import re
import struct


def read_snapshot(path):
    text = path.read_text(errors='replace')
    if '\nSNAPSHOT_COMPLETE\n' not in text:
        raise ValueError('Incomplete snapshot')
    header = re.search(r'^SNAPSHOT selector=([0-9a-f]+) width=([0-9a-f]+) groups=([0-9a-f]+)$',text,re.M)
    if not header:
        raise ValueError('Missing runtime library header')
    rows, current, words = {}, None, []
    for line in text.splitlines():
        m = re.fullmatch(r'ROW ([0-9a-f]+) ([0-9a-f]+)',line)
        if m:
            if current is not None:
                raise ValueError('Truncated row')
            current, words = tuple(int(x,16) for x in m.groups()), []
        elif current is not None:
            m = re.fullmatch(r'[0-9a-f]{8}`?[0-9a-f]{8}\s+((?:[0-9a-f]{8}\s*){4})',line)
            if m:
                words.extend(int(v,16) for v in m[1].split())
                if len(words)==12:
                    if current in rows:
                        raise ValueError('Duplicate runtime row')
                    rows[current] = list(struct.unpack('<12f',struct.pack('<12I',*words)))
                    current = None
    if current is not None:
        raise ValueError('Truncated final row')
    return dict(selector=int(header[1],16),width=int(header[2],16),groups=int(header[3],16)), rows


def build(raw, snapshot):
    metadata, runtime = read_snapshot(snapshot)
    extended = raw['extended_55']
    if metadata != dict(selector=1,width=4,groups=9):
        raise ValueError(f'Unverified runtime configuration: {metadata}')
    expected = {(g['index'],r['index']) for g in extended['groups'] for r in g['rows']}
    if set(runtime)!=expected:
        raise ValueError('Runtime/source row coverage mismatch')
    # Verify all values, including negative constraints and zeros.
    factors = [1.0]*4+[1.125]*4+[.9]*4
    for group in extended['groups']:
        for row in group['rows']:
            got = runtime[group['index'],row['index']]
            for i,(a,b) in enumerate(zip(row['values'],got)):
                if not math.isfinite(b) or abs(a*factors[i]-b)>2e-6:
                    raise ValueError(f'Unexpected scaling: group {group["index"]} row {row["index"]} slot {i}')
    lut_group = extended['id_map'][0]
    ff_group = extended['id_map'][2]
    physical = []
    for pin, row_ids in zip('ABCD',[(0,1),(2,3),(4,5),(6,7)]):
        values = [runtime[lut_group,extended['groups'][lut_group]['pin_ids'][i]] for i in row_ids]
        physical.append(dict(internal_pin=pin,source_row_ids=list(row_ids),
            setup_max_ns=max(v for row in values for v in row[6:8]),
            hold_min_ns=min(v for row in values for v in row[8:10])))
    c2q = runtime[ff_group,extended['groups'][ff_group]['pin_ids'][24]]
    return dict(schema='gowin138-synthesis-envelope-v1',part='GW5AST-LV138FPG676AC1/I0',
        units='ns', database_sha256=raw['sha256'],snapshot_sha256=hashlib.sha256(snapshot.read_bytes()).hexdigest(),
        runtime_metadata=metadata,validated_rows=len(runtime),slot_scale_factors=factors,
        scope='Cell delay envelope only. No wire delay, physical pin permutation, clock skew, or complete FF constraints.',
        corners=dict(setup='Slow 0.873V 0C C1/I0',hold='Fast 0.927V 85C C1/I0'),
        physical_lut_inputs=physical,
        lut_envelope_ps=math.ceil(max(p['setup_max_ns'] for p in physical)*1000),
        ff_clock_to_q=dict(group=ff_group,row=24,setup_max_ns=max(c2q[6:8]),hold_min_ns=min(c2q[8:10])),
        runtime_rows=[dict(group=g,row=r,values=vals) for (g,r),vals in sorted(runtime.items())])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--raw',type=Path,default=Path('results/timing/vendor_split/timing.json'))
    ap.add_argument('--snapshot',type=Path,default=Path('results/gowin_snapshot_002/snapshot.log'))
    ap.add_argument('--out',type=Path,required=True)
    args = ap.parse_args()
    result = build(json.loads(args.raw.read_text()),args.snapshot)
    manifest_path = args.snapshot.parent/'manifest.json'
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text())
        if manifest['snapshot_sha256']!=result['snapshot_sha256'] or manifest['rows']!=result['validated_rows']:
            raise ValueError('Capture manifest mismatch')
        result['capture_manifest'] = str(manifest_path)
        result['dll_sha256'] = manifest['dll_sha256']
    args.out.parent.mkdir(parents=True,exist_ok=True)
    args.out.write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps({k:v for k,v in result.items() if k!='runtime_rows'},indent=2))


if __name__ == '__main__':
    main()
