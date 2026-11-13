"""Independent differential check of mixed wide-LUT/FF/ordinary-LUT captures."""
import argparse,json
from pathlib import Path
import numpy as np
from ownpr_device import ROOT,Device,DLL_SHA
from ownpr_image_storage import load_json,sha
from ownpr_lut_configuration import NativeFrameProjection,LutInitChoices
from ownpr_cls_configuration import ClsChoices
from ownpr_ff_configuration import single_ff_attributes
from workspace_budget import reserve
from audit_native_encoding_probe import payload
from ownpr_fuse_audit import SwitchChoices


def audit(folder):
    folder=Path(folder).resolve();meta=load_json(folder/'manifest.json');data=load_json(folder/'encoding.json')
    if meta['status']!='captured' or not meta.get('ordinary_sharing') or meta['dll_sha256']!=DLL_SHA or meta['vendor_pnr_executed'] or meta['device_programmed']:raise ValueError('Unverified mixed calibration')
    if sha(folder/'encoding.json')!=meta['encoding_sha256'] or sha(folder/'probe.vg')!=meta['source_sha256']:raise ValueError('Calibration source changed')
    from ownpr_wide_lut_sharing import calibration as ordinary_calibration
    ordinary_evidence=ordinary_calibration()
    ordinary_rows=load_json(ROOT/'results/native_wide_lut_sharing_001/capture.json')['records']
    device=Device();projection=NativeFrameProjection(device);lut=LutInitChoices(device);cls=ClsChoices(device);switches=SwitchChoices(device);cases={};images={};requirements={};hashes={}
    def read_image(item):
        path=folder/item['file']
        if path.parent!=folder or path.suffix!='.gz' or sha(path)!=item['compressed_sha256']:raise ValueError('Image provenance changed')
        from ownpr_clock_images import image_sha
        if image_sha(path)!=item['original_sha256']:raise ValueError('Decoded image changed')
        hashes[path.name]=sha(path);return projection.read(path)
    for row in data['records']:
        r,c=row['site'];level=row['level'];mixed=row['ordinary_sharing'];key=(r,c,level,mixed)
        if key in cases or level not in (5,6) or row['slot']!={5:0,6:1}[level] or device.grid[r-1][c-1]!=17 or row['tile_type']!=17 or row['source_fse_sha256']!=device.fse.sha256:raise ValueError('Unsupported mixed case')
        if not row['native_edge_present'] or not row['logical_connection_verified'] or row['ordinary_input_conflict'] or not row['stable_baseline'] or not row['full_payload_restored']:raise ValueError('Invalid native edge/restoration evidence')
        if any(v for values in row['native_wire_delay_by_selector'].values() for v in values):raise ValueError('Wide FF direct edge has nonzero delay')
        occupied=set(range(1<<(level-4)));extras=set(range(8))-occupied if mixed else set()
        if row['ordinary_slots']!=sorted(extras):raise ValueError('Unexpected extra slots')
        attributes=single_ff_attributes(slot=row['slot'],**row['ff_profile']);req={}
        for slot in occupied|extras:attributes[slot//2][0]=9
        for index,values in enumerate(attributes):req.update(cls.requirements(r-1,c-1,index,values))
        for slot in occupied|extras:
            entries=lut.requirements(r-1,c-1,slot,0xA639)
            if any(bit in req and req[bit]!=v for bit,v in entries.items()):raise ValueError('Independent fuse conflict')
            req.update(entries)
        image=read_image(row['linked']);base=read_image(row['baseline']);restored=read_image(row['restored']);warmup=read_image(row['warmup'])
        slot=row['slot']
        if row['source']!=[r,c,48+slot] or row['destination']!=[r,c,295+slot]:raise ValueError('Unexpected wide FF endpoint')
        link=switches.requirements((r-1,c-1,'OF'+str(slot),'DI'+str(slot)))
        expected={projection.index(bit) for bit,v in link.items() if int(base[projection.index(bit)])-48!=v}
        if not expected or set(np.flatnonzero(base!=image).tolist())!=expected or any(int(image[projection.index(bit)])-48!=v for bit,v in link.items()):raise ValueError('Independent direct-link selector mismatch')
        if not np.array_equal(base,restored) or not np.array_equal(base,warmup):raise ValueError('Frame restoration/stability mismatch')
        if payload(folder/row['baseline']['file'])!=payload(folder/row['restored']['file']) or payload(folder/row['baseline']['file'])!=payload(folder/row['warmup']['file']):raise ValueError('Full payload restoration/stability mismatch')
        if attributes!=row['linked']['attributes'] or any(int(image[projection.index(b)])-48!=v for b,v in req.items()):raise ValueError('Independent mixed model mismatch')
        pins=row['mixed_pin_nodes'];ff='reg_'+str(level)
        if set(pins)!={ff}|{f'extra_{s}' for s in extras} or any(len(p)!=5 or any(v['node'] is None for v in p) for p in pins.values()):raise ValueError('Incomplete mixed pin binding')
        reference=next(v for v in ordinary_rows if v['site']==[r,c] and v['level']==level and v['label']=='ordinary')
        if any(pins[f'extra_{s}']!=reference['pins'][f'lut_{8+s}'] for s in extras):raise ValueError('Ordinary LUT binding differs from standalone calibration')
        ff_nodes={tuple(p['node']) for p in pins[ff]};ordinary_nodes=[tuple(p['node']) for n,ps in pins.items() if n!=ff for p in ps]
        if len(set(ordinary_nodes))!=len(ordinary_nodes) or ff_nodes&set(ordinary_nodes):raise ValueError('Mixed pin collision')
        cases[key]=row;images[key]=image;requirements[key]=req
    if set(cases)!={(r,90,l,m) for r in (50,51) for l in (5,6) for m in (False,True)}:raise ValueError('Incomplete two-site two-level pair coverage')
    records=[]
    for r in (50,51):
        for level in (5,6):
            a,b=(r,90,level,False),(r,90,level,True);wide,mixed=cases[a],cases[b]
            if wide['ff_profile']!=mixed['ff_profile'] or wide['source']!=mixed['source'] or wide['destination']!=mixed['destination'] or wide['mixed_pin_nodes']['reg_'+str(level)]!=mixed['mixed_pin_nodes']['reg_'+str(level)]:raise ValueError('Sharing changed FF controls or bindings')
            changed=set(np.flatnonzero(images[a]!=images[b]).tolist());allowed=set()
            for slot in mixed['ordinary_slots']:allowed.update(projection.index(bit) for bit in lut.requirements(r-1,89,slot,0xA639))
            allowed.update(projection.index(bit) for bit,v in requirements[b].items() if bit in requirements[a] and requirements[a][bit]!=v)
            if changed-allowed:raise ValueError('Sharing changed bits outside ordinary LUT/CLS activation')
            records.append(dict(site=[r,90],level=level,ff_slot=wide['slot'],ff_profile=wide['ff_profile'],ordinary_slots=mixed['ordinary_slots'],changed_bits=len(changed),direct_edge_zero_delay=True,independent_model_matched=True))
    return dict(status='isolated_wide_ff_ordinary_composition_verified',records=records,dll_sha256=DLL_SHA,fse_sha256=device.fse.sha256,source_manifest_sha256=sha(folder/'manifest.json'),source_encoding_sha256=sha(folder/'encoding.json'),ordinary_binding_calibration_sha256=ordinary_evidence['admission_sha256'],image_sha256=hashes,production_enabled=False,full_routed_sta_verified=False,hardware_verified=False)


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--capture',type=Path,required=True);p.add_argument('--out',type=Path,required=True);a=p.parse_args();reserve(ROOT,1000000)
    result=audit(a.capture)
    with a.out.open('x') as f:json.dump(result,f,indent=2)
    print(json.dumps({k:v for k,v in result.items() if k!='image_sha256'},indent=2))
