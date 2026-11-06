"""Narrow, evidence-gated ALU[0:5] / unpaired ordinary LUT[6:7] sharing."""
from functools import lru_cache
from ownpr_device import ROOT,DLL_SHA
from ownpr_image_storage import load_json,sha

@lru_cache(maxsize=1)
def calibration():
    p=ROOT/'results/native_carry_lut_sharing_005';m=load_json(p/'manifest.json');a=load_json(p/'audit.json');c=load_json(p/'capture.json')
    if m['status']!='captured' or m['dll_sha256']!=DLL_SHA or m['vendor_pnr_executed'] or m['open_chip_database_used'] or m['audit_sha256']!=sha(p/'audit.json') or a['capture_sha256']!=sha(p/'capture.json'):raise ValueError('Unverified carry/LUT sharing capture')
    if a['status']!='mixed_carry_lut_encoding_and_binding_verified' or not all(a[k] for k in ('requirements_match','pin_bindings_unchanged','ordinary_bitmap_composition_match','full_payload_restored')):raise ValueError('Mixed carry/LUT checks incomplete')
    if a['alu_slots']!=list(range(6)) or a['lut_slots']!=[6,7] or a['tile_type']!=17 or a['ff_sharing_supported']:raise ValueError('Unexpected mixed carry/LUT scope')
    if len(c['records'])!=6 or any(r['mismatches'] or not r['full_payload_restored'] for r in c['records']):raise ValueError('Incomplete mixed-mode cases')
    return a

def check(design,placement,device,cluster):
    a=calibration()
    if a['fse_sha256']!=device.fse.sha256 or placement.get('mixed_carry_lut_calibration')!=a['capture_sha256']:raise ValueError('Carry/LUT sharing is not explicitly admitted')
    if cluster['slot'] not in (6,7) or cluster.get('ff') or cluster.get('wide_ff_root') or design['modules']['top']['cells'][cluster['lut']]['type'] not in ('LUT1','LUT2','LUT3','LUT4'):
        raise ValueError('Only unpaired ordinary LUT slots 6/7 may share a carry CFU')
    r,c=cluster['row'],cluster['column']
    if not (0<=r<len(device.grid) and 0<=c<len(device.grid[r])) or device.grid[r][c]!=17:raise ValueError('Mixed carry/LUT tile type is uncalibrated')
    return a['capture_sha256']

def attributes(placement,row,column,slots):
    from ownpr_alu_configuration import Mode2Configuration
    from ownpr_ff_configuration import independent_ff_groups
    shared=any((p['row'],p['column'])==(row,column) for p in placement['clusters'].values())
    if shared and placement.get('mixed_carry_lut_calibration')!=calibration()['capture_sha256']:raise ValueError('Missing mixed carry/LUT admission')
    return Mode2Configuration.with_alu_modes(independent_ff_groups([]) if shared else [[0]*19 for _ in range(4)],slots),shared
