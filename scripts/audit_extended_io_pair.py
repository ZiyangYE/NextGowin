"""Bind extended I/O observations to their exact source and single-pass image."""
import argparse,json
from pathlib import Path
from native_io_extended_state import compare
from ownpr_image_storage import load_json,sha
from audit_native_encoding_probe import payload


def admitted(folder):
    folder=Path(folder);meta=load_json(folder/'manifest.json');source=Path(meta['source']);name=meta.get('extended_capture_file','pin_feedback.json')
    if name not in ('pin_feedback.json','extended_io_state.json'):raise ValueError('Unsupported extended capture filename')
    data=load_json(folder/name)
    if meta['status']!='captured' or meta['vendor_placement_executed'] or meta['vendor_routing_executed'] or not meta['bitstream_generated']:raise ValueError('Incomplete own-state observation')
    if meta['extended_capture_sha256']!=sha(folder/name) or meta['image_sha256']!=sha(folder/'probe.fs'):raise ValueError('Extended observation changed')
    if data['encoding_phase']!='after_fresh_process_single_encoding':raise ValueError('Wrong encoding phase')
    for input_name,h in meta['input_sha256'].items():
        if sha(source/input_name)!=h:raise ValueError('Observation source changed')
    if payload(source/'probe.fs')!=payload(folder/'probe.fs'):raise ValueError('Observation changed source bitstream payload')
    legacy={tuple(r['site']):r for r in load_json(source/'io_state_after_encoding.json')['io'] if r['site'] is not None}
    if set(legacy)!={tuple(r['site']) for r in data['io'] if r['site'] is not None}:raise ValueError('Legacy IO coverage changed')
    for row in data['io']:
        if row['site'] is None:raise ValueError('Unbound logical IO')
        old=legacy[tuple(row['site'])]
        if row['physical'][:16]!=old['physical'] or [row['logical_supported'][str(i)] for i in range(16)]!=old['logical']:raise ValueError('Legacy attributes changed')
    return data,dict(source_manifest_sha256=sha(source/'manifest.json'),capture_manifest_sha256=sha(folder/'manifest.json'),
        capture_sha256=sha(folder/name),source_payload_identical=True,legacy_first16_equal=True)


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--before',type=Path,required=True);p.add_argument('--after',type=Path,required=True);p.add_argument('--out',type=Path,required=True);a=p.parse_args()
    before,b=admitted(a.before);after,c=admitted(a.after);result=compare(before,after);result.update(before=b,after=c)
    with a.out.open('x') as f:json.dump(result,f,indent=2)
    print(json.dumps(result,indent=2))
