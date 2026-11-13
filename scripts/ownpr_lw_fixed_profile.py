"""Two sampled native longwire fixed edges; no general allocation/capacity model."""
from functools import lru_cache
from pathlib import Path
from ownpr_image_storage import load_json,sha
from ownpr_clock_images import image_path,image_sha
from capture_runtime_tables import EXPECTED

@lru_cache(None)
def calibration():
    root=Path(__file__).resolve().parents[1]/'results/native_parent_fuse_trial_015';report=load_json(root/'longwire_segment_calibration.json')
    if report['status']!='fixed_span_and_branch_plus_programmable_control_verified' or len(report['rows'])!=3:raise ValueError('Unverified longwire segment')
    for row,name,changes in zip(report['rows'],('lw_span','lw_branch','lw_combined'),(0,0,1)):
        folder=root/(name+'_encoding');m=load_json(folder/'manifest.json');c=load_json(folder/'encoding_audit.json')
        if row['case']!=name or row['changed_bits']!=changes or not row['full_payload_restored'] or not row['native_topology_verified']:raise ValueError('Missing fixed/programmable differential controls')
        if m['status']!='captured' or m['dll_sha256']!=EXPECTED or m['vendor_pnr_executed'] or m['open_chip_database_used']:raise ValueError('Longwire source provenance mismatch')
        if sha(folder/'encoding_audit.json')!=row['capture_sha256'] or sha(root/(name+'.json'))!=row['tree_sha256'] or c['clock_tree_sha256']!=row['tree_sha256']:raise ValueError('Longwire tree/capture changed')
        for key in ('baseline','clock','restored'):
            if image_sha(image_path(folder,'encoding_'+key+'.fs'))!=c[key+'_sha256']:raise ValueError('Longwire image changed')
    return sha(root/'longwire_segment_calibration.json')

def fixed(device,source,destination):
    pair=tuple(source),tuple(destination)
    if pair not in (((100,153,282),(98,153,286)),((98,153,288),(98,153,267))):return False
    calibration()
    if device.grid[99][152]!=40 or device.grid[97][152]!=17:raise ValueError('Longwire site types changed')
    return True
