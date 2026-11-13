"""Freeze a complete native mixed-orphan campaign; no route/STA admission."""
import argparse,json
from itertools import product
from pathlib import Path
from ownpr_device import ROOT,DLL_SHA
from ownpr_image_storage import load_json,sha
from ownpr_clock_images import image_sha
from audit_native_encoding_probe import payload


def audit(folder):
    hashes={};rows=[];attrs={};fse=set()
    def retain(path):hashes[path.resolve().relative_to(ROOT).as_posix()]=sha(path)
    for ce,base,site in product(('one','signal'),(0,2,4),range(2)):
        p=folder/f'encoding_{ce}_slot{base}_site{site}_v2';m=load_json(p/'manifest.json');a=load_json(p/'encoding_audit.json')
        if m['status']!='captured' or m['dll_sha256']!=DLL_SHA or any(m[k] for k in ('vendor_placement_executed','vendor_routing_executed','open_chip_database_used','device_programmed')):raise ValueError('Independent native capture required')
        if sha(p/'encoding_audit.json')!=m['encoding_audit_sha256']:raise ValueError('Capture audit changed')
        for name,digest in m['source_sha256'].items():
            if sha(p/name)!=digest:raise ValueError('Capture source changed')
            retain(p/name)
        if a['status']!='mixed_orphan_encoding_matched' or a['base_slot']!=base or a['ce']!=ce or a['site']!=[(50,70)[site],90] or a['tile_type']!=17:raise ValueError('Unexpected mixed orphan case')
        if not a['full_payload_restored'] or a['baseline_mismatches'] or a['unused_lut_encoded_init']!=65535 or len(a['checks'])!=2:raise ValueError('Incomplete reversible encoding')
        for check,label in zip(a['checks'],('orphan_with_even_lut','orphan')):
            if check['label']!=label or not check['attributes_match'] or not check['direct_data'] or any(check[k] for k in ('missing','unexpected','value_mismatches')) or check['actual_changes']!=check['expected_changes']:
                raise ValueError('Encoding delta or paired data mismatch')
        for name in ('baseline','orphan_with_even_lut','orphan','restored'):
            row=a[name];file=p/row['file']
            if sha(file)!=row['compressed_sha256'] or image_sha(file)!=row['original_sha256']:raise ValueError('Native encoded witness changed')
            if not all(row['remaining_paired_data_direct']):raise ValueError('Remaining FF data lost its direct connection')
            retain(file)
        # Creation-time comments may differ. Compare every non-comment command,
        # frame, CRC and trailer, not merely the projected configuration bits.
        if payload(p/a['baseline']['file'])!=payload(p/a['restored']['file']):raise ValueError('Full encoded payload was not restored')
        mode={k:a[k]['attributes'] for k in ('baseline','orphan_with_even_lut','orphan')}
        if (ce,base) in attrs and attrs[ce,base]!=mode:raise ValueError('Mixed orphan mode attributes vary across sites')
        attrs[ce,base]=mode;fse.add(a['source_fse_sha256']);retain(p/'manifest.json');retain(p/'encoding_audit.json')
        rows.append({'folder':p.relative_to(ROOT).as_posix(),'base_slot':base,'ce':ce,'site':a['site'],'checks':a['checks']})
    if len(fse)!=1:raise ValueError('FSE changed within campaign')
    return {'status':'mixed_orphan_campaign_verified','dll_sha256':DLL_SHA,'source_fse_sha256':next(iter(fse)),
            'case_count':len(rows),'records':rows,'source_sha256':hashes,'routing_verified':False,'timing_signoff':False,
            'scope':'Type17 ordinary paired DFFRE, INIT0, RESET0, CE1 or dynamic, positive clock; odd slots1/3/5 with other independent DFFRE/LUT logic. No arithmetic/wide/independent-data sharing.'}


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--folder',type=Path,required=True);p.add_argument('--out',type=Path,required=True);a=p.parse_args()
    report=audit(a.folder.resolve())
    with a.out.open('x') as f:json.dump(report,f,indent=2);f.write('\n')
    print(json.dumps({'status':report['status'],'cases':report['case_count'],'sha256':sha(a.out)}))
