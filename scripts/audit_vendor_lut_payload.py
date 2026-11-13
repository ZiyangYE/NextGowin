"""Check native physical LUT truth words against encoded FSE-addressed bits."""
import argparse,json
from pathlib import Path
from ownpr_device import Device,ROOT
from ownpr_image_storage import load_json,sha
from ownpr_lut_configuration import LutInitChoices,NativeFrameProjection
from workspace_budget import reserve

def audit(folder):
    bindings=load_json(folder/'primitive_bindings.json');d=Device();choices=LutInitChoices(d);projection=NativeFrameProjection(d);image=projection.read(folder/'probe.fs');sites={};owners={}
    def add(header,word,name):
        kind,r,c,slot=header
        if kind!=24:raise ValueError('Unsupported LUT physical kind')
        key=(r-1,c-1,slot)
        if key in sites and sites[key]!=word:raise ValueError('Conflicting LUT ownership/truth word')
        sites[key]=word;owners.setdefault(key,[]).append(name)
    for row in bindings:
        if row.get('physical_class')=='.?AVlut4@NETLIST@@':add(row['physical_header'],row['physical_init_word'],row['name'])
        companion=row.get('ff_companion_lut')
        if companion:add(companion['header'],companion['init_word'],row['name']+'/companion')
    bits={};mismatches=[]
    for (r,c,s),word in sites.items():
        for bit,value in choices.requirements(r,c,s,word).items():
            if bit in bits and bits[bit]!=value:raise ValueError('Conflicting encoded LUT bits')
            bits[bit]=value
            actual=int(image[projection.index(bit)])-48
            if actual!=value:mismatches.append(dict(site=[r,c,s],bit=bit,expected=value,actual=actual))
    if not sites:raise ValueError('No native LUTs observed')
    return dict(status='physical_lut_words_match_encoded_payload' if not mismatches else 'failed',lut_sites=len(sites),truth_bits=len(bits),mismatches=mismatches,
        source_sha256={str(p):sha(p) for p in (folder/'primitive_bindings.json',folder/'probe.fs')},fse_sha256=d.fse.sha256,
        scope='All captured physical LUTs including FF companions. Does not prove register/control/mux/carry/clock/routing configuration semantics or complete hardware behavior.')

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('folders',nargs='+',type=Path);p.add_argument('--out',type=Path,required=True);a=p.parse_args();reserve(ROOT,1000000)
    rows=[dict(folder=str(f),**audit(f)) for f in a.folders]
    with a.out.open('x') as stream:json.dump(rows,stream,indent=2)
    print([(r['folder'],r['status'],r['lut_sites'],len(r['mismatches'])) for r in rows])
    if any(r['status']!='physical_lut_words_match_encoded_payload' for r in rows):raise SystemExit(1)
