"""Explicit experimental admission for independently calibrated mixed CFUs."""
from functools import lru_cache
from ownpr_device import ROOT,DLL_SHA
from ownpr_image_storage import load_json,sha

ADMISSION_SHA256='e6b4ede10f5259a2afdbc8b3c0cc244e6589bfebe708f1defa2678d322c046c4'


def supports_profile(design,ff_name):
    cells=design['modules']['top']['cells'];ff=cells[ff_name];constants={'0':'zero','1':'one'}
    for c in cells.values():
        if c['type'] in ('GND','VCC'):
            for bs in c['connections'].values():
                for b in bs:constants[b]='zero' if c['type']=='GND' else 'one'
    return (ff['type']=='DFFRE' and int(ff.get('parameters',{}).get('INIT','0'),2)==0
        and constants.get(ff['connections']['CE'][0],'signal')=='signal'
        and constants.get(ff['connections']['RESET'][0],'signal')=='zero'
        and constants.get(ff['connections']['CLK'][0],'signal')=='signal')


@lru_cache(maxsize=1)
def calibration():
    folder=ROOT/'results/native_wide_ff_ordinary_sharing_002'
    report=folder/'independent_audit.json'
    if sha(report)!=ADMISSION_SHA256:raise ValueError('Mixed wide FF calibration is not admitted')
    a=load_json(report);m=load_json(folder/'manifest.json')
    if a['status']!='isolated_wide_ff_ordinary_composition_verified' or a['dll_sha256']!=DLL_SHA or a['source_manifest_sha256']!=sha(folder/'manifest.json') or a['source_encoding_sha256']!=sha(folder/'encoding.json') or m['source_sha256']!=sha(folder/'probe.vg'):raise ValueError('Mixed wide FF evidence changed')
    if m['status']!='captured' or m['vendor_pnr_executed'] or m['device_programmed'] or a['production_enabled']:raise ValueError('Unverified mixed wide FF scope')
    for name,digest in a['image_sha256'].items():
        p=(folder/name).resolve()
        if p.parent!=folder.resolve() or sha(p)!=digest:raise ValueError('Mixed wide FF image changed')
    if {(tuple(r['site']),r['level']) for r in a['records']}!={(site,level) for site in ((50,90),(51,90)) for level in (5,6)}:raise ValueError('Incomplete mixed coverage')
    return dict(a,admission_sha256=ADMISSION_SHA256)


def check(design,placed,device,macro,spec,extras):
    if macro.get('wide_ff_ordinary_sharing') is not True or placed.get('mixed_wide_ff_lut_calibration')!=ADMISSION_SHA256:raise ValueError('FF sharing requires explicit mixed calibration admission')
    a=calibration();r,c=macro['row'],macro['column'];level=spec['level'];cells=design['modules']['top']['cells']
    if device.grid[r][c]!=17 or device.fse.sha256!=a['fse_sha256'] or level not in (5,6) or macro['root_slot']!={5:0,6:1}[level]:raise ValueError('Uncalibrated mixed wide FF site')
    if macro.get('wide_ff_ordinary_sharing') is not True or placed.get('mixed_wide_ff_lut_calibration')!=ADMISSION_SHA256:raise ValueError('Mixed wide FF sharing requires explicit admission')
    occupants=[v for v in placed['clusters'].values() if (v['row'],v['column'])==(r,c)]
    ffs=[v for v in occupants if v.get('ff')]
    if len(ffs)!=1 or ffs[0].get('wide_ff_root')!=macro['root'] or ffs[0]['slot']!=macro['root_slot'] or ffs[0]['lut'] not in spec['leaves']:raise ValueError('Unsupported mixed FF binding')
    ff=cells[ffs[0]['ff']];constants={'0':'zero','1':'one'}
    for cell in cells.values():
        if cell['type'] in ('GND','VCC'):
            for bits in cell['connections'].values():
                for bit in bits:constants[bit]='zero' if cell['type']=='GND' else 'one'
    if ff['type']!='DFFRE' or ff['connections']['D']!=cells[macro['root']]['connections']['O'] or int(ff.get('parameters',{}).get('INIT','0'),2)!=0:raise ValueError('Unsupported mixed FF semantics')
    profile=dict(cell_type='DFFRE',ce=constants.get(ff['connections']['CE'][0],'signal'),control=constants.get(ff['connections']['RESET'][0],'signal'),clock=constants.get(ff['connections']['CLK'][0],'signal'))
    if profile!=dict(cell_type='DFFRE',ce='signal',control='zero',clock='signal') or not all(row['ff_profile']==profile for row in a['records']):raise ValueError('Uncalibrated mixed FF control profile')
    allowed=set(range(1<<(level-4),8));used=set()
    for name,row in extras.items():
        if row.get('ff') or row.get('wide_ff_root') or cells[name]['type'] not in ('LUT1','LUT2','LUT3','LUT4') or row['slot'] not in allowed or row['slot'] in used:raise ValueError('Unsupported mixed ordinary LUT')
        used.add(row['slot'])
    return ADMISSION_SHA256
