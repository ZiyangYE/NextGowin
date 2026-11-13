"""Readback support for one reversibly calibrated I/O zero-select PIP."""
from functools import lru_cache
from pathlib import Path
from ownpr_image_storage import load_json,sha
from ownpr_clock_images import image_path,image_sha
from capture_runtime_tables import EXPECTED

@lru_cache(None)
def calibration():
    root=Path(__file__).resolve().parents[1]/'results/native_parent_fuse_trial_015';report=load_json(root/'io_default_calibration.json')
    if report['status']!='default_and_explicit_input_choices_verified':raise ValueError('Unverified IO default selector')
    for row,expected in zip(report['rows'],(0,2)):
        folder=root/(row['case']+'_encoding');meta=load_json(folder/'manifest.json');capture=load_json(folder/'encoding_audit.json')
        if meta['status']!='captured' or meta['dll_sha256']!=EXPECTED or meta['vendor_pnr_executed'] or meta['open_chip_database_used']:raise ValueError('IO calibration provenance changed')
        if row['status']!='isolated_pip_bits_matched' or row['changed_bits']!=expected or row['mask_bits']!=10 or not row['full_payload_restored'] or not row['native_topology_verified_by_pointer_resolution']:raise ValueError('IO selector calibration failed')
        if sha(folder/'encoding_audit.json')!=row['capture_sha256'] or sha(root/(row['case']+'.json'))!=row['tree_sha256']:raise ValueError('IO selector evidence changed')
        if capture['clock_tree_sha256']!=row['tree_sha256']:raise ValueError('IO encoder used a different physical tree')
        for file,key in [('encoding_baseline.fs','baseline_sha256'),('encoding_clock.fs','clock_sha256'),('encoding_restored.fs','restored_sha256')]:
            if image_sha(image_path(folder,file))!=capture[key]:raise ValueError('IO selector image changed')
    if len(report['rows'])!=2:raise ValueError('Incomplete IO selector controls')
    return sha(root/'io_default_calibration.json')

def requirements(device,choices,pip):
    if tuple(pip)!=(89,181,'Q6','N260'):return None
    calibration()
    if device.grid[89][181]!=246:raise ValueError('IO tile profile changed')
    table=choices.template(246)
    if ('Q6','N260') in table:raise ValueError('Expected omitted IO input')
    masks=[set(v) for (src,dst),v in table.items() if dst=='N260']
    if not masks or len(masks[0])!=10 or any(m!=masks[0] for m in masks):raise ValueError('IO input mux mask changed')
    return {(89,181,y,x):0 for y,x in masks[0]}
