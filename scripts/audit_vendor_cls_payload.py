"""Check all captured CLS attribute vectors against their native FSE fuse predicates."""
import argparse,json
from pathlib import Path
from ownpr_device import Device,ROOT
from ownpr_image_storage import load_json,sha
from ownpr_lut_configuration import NativeFrameProjection
from ownpr_cls_configuration import ClsChoices
from workspace_budget import reserve

def audit(folder):
    d=Device();projection=NativeFrameProjection(d);image=projection.read(folder/'probe.fs');choices=ClsChoices(d);groups={}
    for row in load_json(folder/'primitive_bindings.json'):
        header=row.get('physical_header')
        if not header:continue
        raw=row.get('cfu_cls_attributes')
        if raw is None and 'cls_attributes' in row:raw=[dict(group=i,attributes=v) for i,v in enumerate(row['cls_attributes'])]
        for group in raw or []:
            key=(header[1]-1,header[2]-1,group['group']);attrs=group['attributes']
            if key in groups and groups[key]!=attrs:raise ValueError('Conflicting cached CLS attributes')
            groups[key]=attrs
    mismatches=[];bits=set()
    for (r,c,g),attrs in groups.items():
        for bit,value in choices.requirements(r,c,g,attrs).items():
            bits.add(bit);actual=int(image[projection.index(bit)])-48
            if actual!=value:mismatches.append(dict(group=[r,c,g],bit=bit,expected=value,actual=actual))
    if not groups:raise ValueError('Empty CLS inventory')
    return dict(status='captured_cls_attributes_match_encoded_bits' if not mismatches else 'failed',groups=[dict(site=list(k),attributes=v) for k,v in sorted(groups.items())],bits=len(bits),mismatches=mismatches,
        bindings_sha256=sha(folder/'primitive_bindings.json'),image_sha256=sha(folder/'probe.fs'),fse_sha256=d.fse.sha256,
        scope='All captured CLS vectors, not every used CFU or every primitive-mode semantic. Coverage and logical interpretation remain separate.')

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('folder',type=Path);p.add_argument('--out',type=Path,required=True);a=p.parse_args();reserve(ROOT,2000000);r=audit(a.folder)
    with a.out.open('x') as f:json.dump(r,f,indent=2)
    print(r['status'],len(r['groups']),r['bits'],len(r['mismatches']))
    if r['status']=='failed':raise SystemExit(1)
