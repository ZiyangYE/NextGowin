"""Admission for native-verified odd-only DFFRE groups in mixed type17 CFUs."""
from functools import lru_cache
from pathlib import Path
from ownpr_image_storage import load_json,sha
from ownpr_device import ROOT,DLL_SHA

ADMISSION=ROOT/'results/dsp_orphan_routing_trial_025/admission.json'
ADMISSION_SHA='fe6753150e11105f0483332b2de12611c2c0bcf9cb434b1d4b806d5d88ee1548'


@lru_cache(maxsize=1)
def calibration():
    if ADMISSION_SHA is None or sha(ADMISSION)!=ADMISSION_SHA:raise ValueError('Mixed orphan FF calibration is not admitted')
    report=load_json(ADMISSION)
    if report['status']!='mixed_orphan_campaign_verified' or report['dll_sha256']!=DLL_SHA or report['case_count']!=12:
        raise ValueError('Incomplete mixed orphan FF campaign')
    for path,digest in report['source_sha256'].items():
        if sha(ROOT/path)!=digest:raise ValueError('Mixed orphan FF evidence changed: '+path)
    return ADMISSION_SHA


def require_site(cells,members,placed,device,site,excluded,constant):
    from ownpr_shared_ff_packing import signature
    if placed.get('orphan_ff_calibration')!=calibration():raise ValueError('Orphan FF site lacks calibration binding')
    if site in excluded or device.grid[site[0]][site[1]]!=17:raise ValueError('Uncalibrated orphan FF site')
    for row in members:
        ff=cells[row['ff']];lut=cells[row['lut']]
        if row.get('wide_ff_root') or row.get('independent_ff_input') or row['slot'] not in range(6):raise ValueError('Uncalibrated orphan FF geometry')
        if signature(ff,constant) is None or lut['type'] not in ('LUT1','LUT2','LUT3','LUT4') or ff['connections']['D']!=lut['connections']['F']:
            raise ValueError('Orphan CFU requires paired default-init DFFREs with RESET0 and CE1/dynamic')
