"""Verify two additional output-IOREG tile types against native observations."""
import argparse,itertools,json
from pathlib import Path
import numpy as np
from ownpr_device import Device,DLL_SHA,ROOT
from ownpr_image_storage import load_json,sha
from ownpr_ioreg_configuration import output_binding,output_attributes,IoregChoices
from ownpr_ioreg_timing import IoregTimingModel
from ownpr_lut_configuration import NativeFrameProjection
from audit_native_encoding_probe import payload


def audit(folder):
    folder=Path(folder);m=load_json(folder/'manifest.json');data=load_json(folder/'ioreg_attributes.json');timing=load_json(folder/'ioreg_timing_queries.json');enc=load_json(folder/'ioreg_encoding_probe.json')
    if m['status']!='captured' or m['dll_sha256']!=DLL_SHA or m['vendor_placement_executed'] or m['vendor_routing_executed'] or len(m['custom_sites'])!=1:raise ValueError('Invalid site-extension capture')
    for file,key in [('probe.vg','source_sha256'),('ioreg_attributes.json','attributes_sha256'),('ioreg_timing_queries.json','timing_sha256'),('ioreg_encoding_probe.json','encoding_probe_sha256')]:
        if sha(folder/file)!=m[key]:raise ValueError('Site-extension source changed')
    site=m['custom_sites'][0];device=Device();binding=output_binding(site);typ=device.grid[binding['D'][0]-1][binding['D'][1]-1]
    if (typ,tuple(site)) not in {(245,(87,183,0)),(63,(110,146,0))}:raise ValueError('Unexpected extension sample')
    seen=set()
    for row in data['records']:
        key=row['cell_type'],row['ce'],row['control'],row['clock']
        if key in seen or row['site']!=site or row['binding']!=binding or row['attributes']!=output_attributes(*key):raise ValueError('Native output binding/control differs')
        if row['native_output_direct'] or row['output_input_route_query']['nodes']!=[[*binding['D'][:2],0]]:raise ValueError('Isolated output query differs from the known unregistered-buffer view')
        seen.add(key)
    if seen!=set(itertools.product(('DFFRE','DFFSE','DFFCE','DFFPE'),('zero','one','signal','inverted'),('zero','one','signal','inverted'),('signal','inverted'))):raise ValueError('Incomplete output-control matrix')
    model=IoregTimingModel();sync=[r for r in timing['registers'] if r.get('primitive')=='DFFRE']
    if len(sync)!=1 or sync[0]['ce_mode']!='one' or sync[0]['control_mode']!='zero' or sync[0]['io_site']!=site:raise ValueError('Missing static synchronous I/O timing case')
    model.check_row(sync[0],'RESET')
    if enc['site']!=site or enc['cell_type']!='DFFRE' or not enc['combined_constant_controls_checked'] or not enc['observed_site_override']:raise ValueError('Missing static sync encoding')
    projection=NativeFrameProjection(device);choices=IoregChoices(device);base=projection.read(folder/enc['baseline']);base_req=choices.observed_requirements(site,output_attributes('DFFRE',**enc['baseline_profile']))
    if sha(folder/enc['baseline'])!=enc['baseline_sha256'] or sha(folder/enc['restored'])!=enc['restored_sha256'] or payload(folder/enc['baseline'])!=payload(folder/enc['restored']):raise ValueError('Encoding baseline/restoration changed')
    for bit,v in base_req.items():
        if int(base[projection.index(bit)])-48!=v:raise ValueError('Baseline I/O bits differ')
    profiles=set();observations=0
    for row in enc['records']:
        if sha(folder/row['variant'])!=row['variant_sha256'] or not row['full_payload_restored']:raise ValueError('I/O perturbation changed or did not restore')
        profile=row['profile'];profiles.add(tuple(profile[k] for k in ('ce','control','clock')));image=projection.read(folder/row['variant']);req=choices.observed_requirements(site,output_attributes('DFFRE',**profile));expected={projection.index(b) for b,v in req.items() if base_req[b]!=v};actual=set(np.flatnonzero(base!=image).tolist())
        if actual!=expected or len(actual)!=row['changed_bits']:raise ValueError('Native I/O perturbation has unexplained bits')
        if any(int(image[projection.index(b)])-48!=v for b,v in req.items()):raise ValueError('Native selected I/O mode differs')
        observations+=len(req)
    if len(profiles)!=8 or ('one','zero','signal') not in profiles:raise ValueError('Incomplete encoding perturbation coverage')
    # The isolated fixture, like the original five-site oracle, leaves the
    # buffer query in its ordinary view. Establish the actual registered
    # output link from an unchanged-state native implementation at this pad.
    reference=ROOT/'results/native_ioreg_contract_trial_016/int2float_ioreg.json';ref=load_json(reference)
    if ref['status']!='output_ioreg_bindings_controls_and_encoded_bits_verified':raise ValueError('Missing registered-output reference')
    for p,h in ref['source_sha256'].items():
        path=Path(p);path=path if path.is_absolute() else ROOT/path
        if sha(path)!=h:raise ValueError('Registered-output reference changed')
    links=[r for r in ref['registers'] if r['site']==site and r['tile_type']==typ and r['direct_output_delay_ns']==0 and r['semantics']==dict(primitive='DFFRE',control_pin='RESET',ce='one',control='zero',clock='signal',initial=0)]
    if len(links)!=1:raise ValueError('No exact fixed-pad static-sync direct-output witness')
    files=[folder/n for n in ('manifest.json','probe.vg','ioreg_attributes.json','ioreg_timing_queries.json','ioreg_encoding_probe.json','extractor_source.py.txt')]+[reference]
    files += [folder/enc[n] for n in ('baseline','restored')]+[folder/r['variant'] for r in enc['records']]
    return dict(status='extra_output_ioreg_static_sync_site_verified',tile_type=typ,site=site,block=0,attribute_cases=len(seen),timing_vectors=len(sync[0]['queries']),encoded_modes=len(profiles),mask_bits=len(base_req),bit_observations=observations,primitive='DFFRE',ce='one',control='zero',clock='signal',native_direct_output_reference=links[0],source_sha256={str(p.resolve()):sha(p) for p in files},scope='Additional A-block tile types, static CE=1 RESET=0 DFFRE only for production admission. Isolated buffer query is not a direct-link witness; exact-pad native reference supplies it. Full clock/data routing, I/O invariants and final native STA remain required.')


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('folder',type=Path);p.add_argument('--out',type=Path,required=True);a=p.parse_args()
    from workspace_budget import reserve
    reserve(ROOT,1000000);r=audit(a.folder)
    with a.out.open('x') as f:json.dump(r,f,indent=2)
    print(r['status'],r['tile_type'],r['timing_vectors'],r['encoded_modes'])
