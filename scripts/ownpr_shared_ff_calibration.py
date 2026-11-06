"""Audit common-control FF attribute queries without enabling shared packing.

Configured paired-LUT data paths, routing and full STA remain separate gates.
"""
import argparse,itertools,json
from pathlib import Path
from ownpr_device import Device,DLL_SHA
from ownpr_place import sha
from ownpr_ff_configuration import FF_CONTROLS,single_ff_attributes


def predict(profiles):
    if len(profiles)!=2:raise ValueError('Expected exactly two FF profiles')
    a,b=sorted(profiles,key=lambda r:r['slot'])
    if a['slot'] not in (0,2,4) or b['slot']!=a['slot']+1:raise ValueError('Uncalibrated shared slots')
    if any(a[k]!=b[k] for k in ('cell_type','ce','control','clock')):raise ValueError('Shared profiles must match')
    result=single_ff_attributes(a['cell_type'],a['slot'],a['ce'],a['control'],a['clock'])
    other=single_ff_attributes(b['cell_type'],b['slot'],b['ce'],b['control'],b['clock'])
    group=a['slot']//2;result[group][14]=other[group][14]
    return result


def verify(rows):
    wanted=set(itertools.product(FF_CONTROLS,(0,2,4),('zero','one','signal','inverted'),('zero','one','signal','inverted'),('signal','inverted')));seen=set()
    paired={bool(r.get('paired_luts',False)) for r in rows}
    if len(paired)!=1:raise ValueError('Mixed paired-data capture modes')
    for row in rows:
        a,b=sorted(row['ffs'],key=lambda r:r['slot']);key=tuple(a[k] for k in ('cell_type','slot','ce','control','clock'))
        if key in seen:raise ValueError('Duplicate shared-control case')
        seen.add(key)
        if predict(row['ffs'])!=row['attributes']:raise ValueError('Shared attributes differ from native conversion')
        if not row['shared_control_nets_identical'] or not row['data_nets_distinct']:raise ValueError('Fixture lacks shared controls or independent data')
        left,right=row['native_pin_nodes']
        if any(left[p] is None or left[p]!=right[p] for p in ('CLK','CE','SR')):raise ValueError('Controls do not share native nodes')
        if any(left[p] is None or right[p] is None or left[p]==right[p] for p in ('D','Q')):raise ValueError('Data/Q physical nodes are not independent')
        if len(row['native_types'])!=2 or row['native_types'][0]!=row['native_types'][1]:raise ValueError('Native primitive types differ')
        if paired=={True}:
            checks=row.get('paired_data_checks',[])
            if (len(checks)!=2 or {r['slot'] for r in checks}!={a['slot'],b['slot']} or
                any(not r['direct'] or r['wrong_slot_direct'] or r['data_route_nodes']!=0 for r in checks)):raise ValueError('Shared paired data was not independently direct')
    if seen!=wanted:raise ValueError('Incomplete common-control profile coverage')
    return dict(status='common_control_attributes_matched',cases=len(seen),groups=[0,1,2],primitive_types=list(FF_CONTROLS),
        shared_control_nodes_verified=True,independent_data_and_output_nodes_verified=True,
        configured_pair_verified=False,paired_lut_data_verified=paired=={True},physical_legality_verified=False,production_packer_enabled=False,
        timing_signoff=False,scope='Attribute and physical-pin observations only. No shared packing, routed paired data, full STA or hardware acceptance inferred.')


def audit(folder):
    folder=Path(folder);meta=json.loads((folder/'manifest.json').read_text());file=folder/'ff_attributes.json';device=Device()
    if (meta['status']!='captured' or meta['dll_sha256']!=DLL_SHA or meta['source_fse_sha256']!=device.fse.sha256 or
        meta['open_chip_database_used'] or meta['vendor_placement_executed'] or meta['vendor_routing_executed'] or sha(file)!=meta['attributes_sha256']):raise ValueError('Unreviewed shared-FF capture provenance')
    data=json.loads(file.read_text());r=verify(data['shared_records']);row,column=data['site']
    if not 1<=row<=device.rows or not 1<=column<=device.cols:raise ValueError('Shared FF site outside device')
    if any(point[:2]!=data['site'] for sample in data['shared_records'] for pins in sample['native_pin_nodes'] for point in pins.values()):raise ValueError('Shared FF pins escaped the captured site')
    r.update(native_site=data['site'],tile_type=device.grid[row-1][column-1])
    r['source_sha256']={str(p.resolve()):sha(p) for p in (folder/'manifest.json',file,folder/'probe.vg',folder/'extractor_source.py.txt')}
    return r


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('folder',type=Path);a=p.parse_args();r=audit(a.folder)
    (a.folder/'shared_control_audit.json').write_text(json.dumps(r,indent=2));print(json.dumps({k:v for k,v in r.items() if k!='source_sha256'},indent=2))
