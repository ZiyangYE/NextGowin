"""Audit the initial clock codec against reversible native data differences."""
import argparse,hashlib,json
from pathlib import Path
import numpy as np
from ownpr_device import Device,DLL_SHA,ROOT
from ownpr_clock_graph import ClockGraph
from ownpr_clock_configuration import ClockChoices
from ownpr_lut_configuration import NativeFrameProjection
from audit_native_encoding_probe import payload
from ownpr_clock_images import image_path,image_sha


def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def audit(folder,tree_file=ROOT/'results/ownpr_clock_tree_001/clock_tree.json',choices=None):
    folder=Path(folder);meta=json.loads((folder/'manifest.json').read_text());capture=json.loads((folder/'encoding_audit.json').read_text())
    if (meta.get('status')!='captured' or meta.get('dll_sha256')!=DLL_SHA or meta.get('open_chip_database_used') is not False
            or meta.get('vendor_pnr_executed') is not False or capture.get('prefix_reference',False)):
        raise ValueError('Unverified direct clock encoding capture')
    if sha(tree_file)!=capture['clock_tree_sha256']:raise ValueError('Clock tree changed')
    for name,key in (('encoding_baseline.fs','baseline_sha256'),('encoding_clock.fs','clock_sha256'),('encoding_restored.fs','restored_sha256')):
        if image_sha(image_path(folder,name))!=capture[key]:raise ValueError('Clock encoding image changed')
    if payload(image_path(folder,'encoding_baseline.fs'))!=payload(image_path(folder,'encoding_restored.fs')):raise ValueError('Complete native payload was not restored')
    if choices is None:
        device=Device();graph=ClockGraph.for_tree(json.loads(Path(tree_file).read_text()));choices=ClockChoices(device,graph)
    else:device=choices.device;graph=choices.graph
    tree=json.loads(Path(tree_file).read_text());values,result=choices.requirements(tree);projection=NativeFrameProjection(device)
    baseline=projection.read(image_path(folder,'encoding_baseline.fs'));clock=projection.read(image_path(folder,'encoding_clock.fs'))
    actual=set(np.flatnonzero(baseline!=clock).tolist());expected={projection.index(b) for b,v in values.items() if baseline[projection.index(b)]-48!=v}
    mismatches=[dict(bit=b,value=v,actual=int(clock[projection.index(b)])-48) for b,v in values.items() if clock[projection.index(b)]-48!=v]
    result.update(status='clock_candidate_bits_matched' if actual==expected and not mismatches and not result['conflicts'] else 'failed',
        required_bits=len(values),predicted_changes=len(expected),actual_changes=len(actual),missing=len(expected-actual),unexpected=len(actual-expected),
        value_mismatches=mismatches,full_payload_restored=True,clock_tree_sha256=sha(tree_file),
        source_fse_sha256=device.fse.sha256,native_capture_sha256=sha(folder/'encoding_audit.json'),topology_sha256=graph.sha256,
        scope='Supported clock candidate: all masked requirements and all changed data bits match native encoding. This is not complete I/O/source-mode, capacity or hardware image verification.')
    return result


if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('folder',type=Path);ap.add_argument('--tree',type=Path);args=ap.parse_args()
    tree=args.tree or args.folder/'clock_tree.json'
    if not tree.exists() and args.tree is None:tree=ROOT/'results/ownpr_clock_tree_001/clock_tree.json'
    r=audit(args.folder,tree)
    (args.folder/'clock_configuration_audit.json').write_text(json.dumps(r,indent=2))
    print(json.dumps({k:v for k,v in r.items() if k not in ('edges','value_mismatches')},indent=2))
    if r['status']=='failed':raise SystemExit(1)
