"""Independently decode isolated wide/ordinary LUT composition evidence."""
import argparse,json,re
from pathlib import Path
import numpy as np
from ownpr_device import ROOT,Device,DLL_SHA
from ownpr_image_storage import load_json,sha
from ownpr_lut_configuration import NativeFrameProjection,LutInitChoices
from ownpr_cls_configuration import ClsChoices
from ownpr_ff_configuration import independent_ff_groups
from audit_native_encoding_probe import payload


def audit(folder):
    folder=Path(folder).resolve();m=load_json(folder/'manifest.json');reported=load_json(folder/'audit.json');capture=load_json(folder/'capture.json')
    if m['status']!='captured' or m['dll_sha256']!=DLL_SHA or m['vendor_pnr_executed'] or m['open_chip_database_used'] or m['device_programmed'] or m['production_enabled']:
        raise ValueError('Unverified isolated capture')
    if sha(folder/'source.py.txt')!=m['source_sha256'] or sha(folder/'probe.vg')!=m['fixture_sha256'] or sha(folder/'audit.json')!=m['audit_sha256'] or sha(folder/'capture.json')!=reported['capture_sha256']:
        raise ValueError('Capture provenance changed')
    if reported['status']!='isolated_wide_lut_composition_verified' or reported['production_enabled']:raise ValueError('Unexpected capture scope')
    device=Device();projection=NativeFrameProjection(device);lut=LutInitChoices(device);cls=ClsChoices(device)
    if reported['fse_sha256']!=device.fse.sha256:raise ValueError('Device tables changed')
    def image_path(name):
        p=(folder/name).resolve()
        if p.parent!=folder or p.suffix!='.gz':raise ValueError('Unexpected image path')
        return p
    basefile=image_path(capture['baseline']);base=projection.read(basefile);hashes={basefile.name:sha(basefile)}
    if payload(basefile)!=payload(folder/'probe.fs'):raise ValueError('Final native state not restored')
    words={name:int(word,16) for name,word in re.findall(r'defparam\s+(\w+)\.INIT=16\x27h([\dA-Fa-f]+);',(folder/'probe.vg').read_text())}
    rows={};records=[]
    for row in capture['records']:
        r,c=row['site'];level=row['level'];label=row['label'];key=(r,c,level,label)
        if key in rows or row['tile_type']!=17 or device.grid[r-1][c-1]!=17 or level not in (5,6) or label not in ('wide','ordinary','mixed') or row['mismatches'] or not row['full_payload_restored']:
            raise ValueError('Unexpected or failed calibration case')
        leaves={'lut_0':0,'lut_1':1} if level==5 else {'lut_0':2,'lut_1':3,'lut_2':0,'lut_3':1}
        extras={f'lut_{8+s}':s for s in range(8) if s not in leaves.values()}
        if row['leaf_slots']!=sorted(leaves.values()) or row['ordinary_slots']!=sorted(extras.values()):raise ValueError('Uncalibrated sharing slots')
        active={**(leaves if label!='ordinary' else {}),**(extras if label!='wide' else {})};groups=independent_ff_groups([]);req={}
        for s in active.values():groups[s//2][0]=9
        for i,attributes in enumerate(groups):req.update(cls.requirements(r-1,c-1,i,attributes))
        for name,s in active.items():
            entries=lut.requirements(r-1,c-1,s,words[name])
            if any(b in req and req[b]!=v for b,v in entries.items()):raise ValueError('Model fuse conflict')
            req.update(entries)
        path=image_path(row['variant']);image=projection.read(path);hashes[path.name]=sha(path)
        if len(req)!=row['requirements'] or any(int(image[projection.index(b)])-48!=v for b,v in req.items()):raise ValueError('Decoded native LUT/CLS bits differ from model')
        rows[key]=(row,image)
    sites=sorted({(r,c) for r,c,_,_ in rows})
    if len(sites)!=2 or len(rows)!=12 or sites!=sorted(map(tuple,reported['sites'])):raise ValueError('Incomplete two-site coverage')
    for r,c in sites:
        for level in (5,6):
            (w,wi),(o,oi),(x,xi)=[rows[r,c,level,k] for k in ('wide','ordinary','mixed')]
            if {**w['pins'],**o['pins']}!=x['pins'] or not np.array_equal(xi,np.where(wi!=base,wi,oi)):raise ValueError('Mixed binding/bitmap interaction')
            def nodes(names,direction):return {tuple(p['node']) for n in names for p in x['pins'][n] if p['direction']==direction and p['node'] is not None}
            if nodes(o['pins'],'In')&nodes(w['pins'],'In') or nodes(o['pins'],'Out')&nodes([f'mux_{level}_0'],'Out'):raise ValueError('External pin node conflict')
            if any(len(p)!=5 or any(v['node'] is None for v in p) for p in o['pins'].values()):raise ValueError('Incomplete ordinary pin binding')
            records.append(dict(site=[r,c],level=level,ordinary_slots=o['ordinary_slots'],leaf_slots=w['leaf_slots'],combined_requirements=x['requirements'],combined_pin_count=sum(len(p) for p in x['pins'].values())))
    return dict(status='independent_wide_lut_composition_verified',records=records,source_capture_sha256=sha(folder/'capture.json'),source_manifest_sha256=sha(folder/'manifest.json'),image_sha256=hashes,
        dll_sha256=DLL_SHA,fse_sha256=device.fse.sha256,production_enabled=False,full_route_sta_verified=False,ff_sharing_supported=False,
        scope='Type-17 isolated LUT5 root slot 0 / LUT6 root slot 1 plus remaining ordinary LUT slots; no FF/ALU sharing, routing, loaded timing or hardware certification.')


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--capture',type=Path,required=True);p.add_argument('--out',type=Path,required=True);a=p.parse_args()
    from workspace_budget import reserve
    reserve(ROOT,1000000);result=audit(a.capture);a.out.write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result,indent=2))
