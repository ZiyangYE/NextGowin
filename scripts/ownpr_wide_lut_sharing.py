"""Evidence-gated type-17 LUT5/6 sharing with unpaired ordinary LUTs."""
from functools import lru_cache
from ownpr_device import ROOT,DLL_SHA
from ownpr_image_storage import load_json,sha


@lru_cache(maxsize=1)
def calibration():
    folder=ROOT/'results/native_wide_lut_sharing_001';m=load_json(folder/'manifest.json');a=load_json(folder/'independent_audit.json');native=load_json(folder/'audit.json')
    if m['status']!='captured' or m['dll_sha256']!=DLL_SHA or m['vendor_pnr_executed'] or m['open_chip_database_used'] or m['device_programmed']:
        raise ValueError('Unverified wide/LUT calibration')
    if a['status']!='independent_wide_lut_composition_verified' or a['source_manifest_sha256']!=sha(folder/'manifest.json') or a['source_capture_sha256']!=sha(folder/'capture.json') or m['audit_sha256']!=sha(folder/'audit.json') or native['capture_sha256']!=a['source_capture_sha256']:
        raise ValueError('Wide/LUT calibration provenance changed')
    if sha(folder/'probe.vg')!=m['fixture_sha256'] or sha(folder/'source.py.txt')!=m['source_sha256']:raise ValueError('Calibration fixture/source changed')
    for name,h in a['image_sha256'].items():
        p=(folder/name).resolve()
        if p.parent!=folder.resolve() or sha(p)!=h:raise ValueError('Wide/LUT image changed')
    rows={(tuple(r['site']),r['level']):r for r in a['records']}
    if len(rows)!=4 or {r[0] for r in rows}!={(50,90),(51,90)}:raise ValueError('Incomplete wide/LUT coverage')
    for site in ((50,90),(51,90)):
        for level in (5,6):
            row=rows[site,level];count=1<<(level-4)
            if row['ordinary_slots']!=list(range(count,8)) or row['leaf_slots']!=list(range(count)):raise ValueError('Unexpected wide/LUT slots')
    return dict(a,admission_sha256=sha(folder/'independent_audit.json'))


def check(design,placement,device,macro,spec,extras):
    evidence=calibration();level=spec['level'];r,c=macro['row'],macro['column']
    if level not in (5,6) or macro['root_slot']!={5:0,6:1}[level] or device.grid[r][c]!=17 or evidence['fse_sha256']!=device.fse.sha256:
        raise ValueError('Uncalibrated shared wide macro')
    if macro.get('ordinary_sharing') is not True or placement.get('mixed_wide_lut_calibration')!=evidence['admission_sha256']:raise ValueError('Wide/LUT sharing is not explicitly admitted')
    allowed=set(range(1<<(level-4),8));occupied=set(spec['leaves'].values());cells=design['modules']['top']['cells']
    for name,row in placement['clusters'].items():
        if (row['row'],row['column'])==(r,c) and (row.get('ff') or row.get('wide_ff_root')):raise ValueError('FF sharing with ordinary/wide LUTs is not calibrated')
    for name,row in extras.items():
        if cells[name]['type'] not in ('LUT1','LUT2','LUT3','LUT4') or row['slot'] not in allowed or row['slot'] in occupied:raise ValueError('Conflicting ordinary LUT in wide macro')
        occupied.add(row['slot'])
    return evidence['admission_sha256']
