"""Audit independent FF selector observations and native mode/fuse witnesses."""
import argparse,json
from pathlib import Path
import numpy as np
from ownpr_device import Device,DLL_SHA,ROOT
from ownpr_image_storage import load_json,sha
from ownpr_independent_ff_inputs import groups,selector
from ownpr_lut_configuration import NativeFrameProjection,LutInitChoices
from ownpr_cls_configuration import ClsChoices
from ownpr_fuse_audit import SwitchChoices
from audit_native_encoding_probe import payload


def discovery(folder):
    meta=load_json(folder/'manifest.json');data=load_json(folder/'routes.json');seen=set();conflicts=[]
    if meta['status']!='captured' or not meta.get('finalize_independent') or meta['dll_sha256']!=DLL_SHA or meta['routes_sha256']!=sha(folder/'routes.json'):raise ValueError('Incomplete independent-input discovery')
    if not data['saved_physical_fields_restored'] or data['production_enabled']:raise ValueError('Unexpected discovery scope')
    for r in data['records']:
        typ,mode,slot=r['tile_type'],r['independent_input_mode'],r['ff_slot'];key=typ,mode,slot
        if key in seen:raise ValueError('Duplicate discovery case')
        seen.add(key);shared=slot in r['occupied_lut_slots'];node=('SEL' if shared else 'D')+str(slot)
        if r['data_names']!=[node] or r['direct'] or r['leaf_input_conflict']:raise ValueError('Independent data terminal changed')
        prediction=groups(slot,**r['ff_profile'])
        # The oracle explicitly activates its FF group; other wide leaf-group
        # activation is only part of encoded captures, not route queries.
        if prediction!=r['group_attributes']:raise ValueError('Independent FF attributes differ')
        before,after=r['finalizer_before'],r['finalizer_after']
        expected=[v.copy() for v in before];expected[slot//2][11+(slot&1)]=1
        if after!=expected:raise ValueError('Native finalizer changed unexpected fields')
        expected_conflict=shared and slot in r['occupied_mux_slots']
        if r['selector_input_conflict']!=expected_conflict:raise ValueError('Selector conflict inventory differs')
        if expected_conflict:
            try:selector(slot,r['occupied_mux_slots'])
            except ValueError:conflicts.append(key)
            else:raise ValueError('Resource-conflicting combination admitted')
    if seen!={(t,m,s) for t in (17,228) for m in ('none','companion','5','6','7') for s in range(8)}:raise ValueError('Incomplete discovery matrix')
    return dict(status='independent_selector_and_mode_queries_verified',cases=len(seen),rejected_selector_conflicts=len(conflicts),scope='One FF and bound LUT/MUX fixtures; mode finalizer uses a device-kind-70 adapter, not vendor placement/routing. Multi-FF controls and full STA are separate.',source_sha256={str(p):sha(p) for p in (folder/'routes.json',folder/'manifest.json',folder/'source.py.txt')})


def encoding(folder):
    meta=load_json(folder/'manifest.json');d=Device();projection=NativeFrameProjection(d);cls=ClsChoices(d);luts=LutInitChoices(d);switch=SwitchChoices(d);rows=[]
    if meta['status']!='captured' or not meta.get('finalize_independent') or meta['dll_sha256']!=DLL_SHA or meta['encoding_sha256']!=sha(folder/'encoding.json'):raise ValueError('Incomplete encoded independent-input capture')
    for r in load_json(folder/'encoding.json')['records']:
        if r['mode']!='companion' or r['production_enabled'] or r['full_sta_verified']:raise ValueError('Unsupported independent-input claim')
        rr,cc=r['site'];slot=r['slot'];expected=groups(slot);images={}
        for label in ('first','stable','repeat','linked','restored'):
            rec=r[label];path=folder/rec['file']
            if path.parent.resolve()!=folder.resolve() or sha(path)!=rec['compressed_sha256'] or sha(path.with_suffix(''))!=rec['original_sha256']:raise ValueError('Encoded input witness changed')
            if rec['attributes']!=expected:raise ValueError('Encoded independent-input attributes differ')
            images[label]=projection.read(path)
        if payload(folder/r['stable']['file'])!=payload(folder/r['restored']['file']) or payload(folder/r['stable']['file'])!=payload(folder/r['repeat']['file']):raise ValueError('Independent input encoding did not restore/stabilize')
        requirements=luts.requirements(rr-1,cc-1,slot,0xa639)
        for g,attrs in enumerate(expected):requirements.update(cls.requirements(rr-1,cc-1,g,attrs))
        for image in images.values():
            if any(int(image[projection.index(b)])-48!=v for b,v in requirements.items()):raise ValueError('Independent mode/LUT encoded bits differ')
        pip=(rr-1,cc-1,d.names[r['parent'][2]],selector(slot));bits=switch.requirements(pip);before=images['stable'];after=images['linked']
        wanted={projection.index(b) for b,v in bits.items() if int(before[projection.index(b)])-48!=v};actual=set(np.flatnonzero(before!=after).tolist())
        if not wanted or wanted!=actual or any(int(after[projection.index(b)])-48!=v for b,v in bits.items()):raise ValueError('Independent selector route delta differs')
        rows.append(dict(tile_type=r['tile_type'],slot=slot,mode_bits=len(requirements),selected_pip=pip,pip_mask_bits=len(bits),changed_route_bits=len(actual),terminal_to_raw_edge=r['terminal_to_raw_edge'],isolated_wire_query=r['native_wire_delay_by_selector']))
    return dict(status='sample_independent_input_modes_and_fuses_verified',records=rows,full_sta_verified=False,production_enabled=False,scope='Exclusive one-FF/one-unrelated-LUT captures. Zero isolated wire values are not a path-delay model; no SEL-to-DI RRG edge exists in these observations.',source_sha256={str(p):sha(p) for p in (folder/'encoding.json',folder/'routes.json',folder/'manifest.json',folder/'independent_encoding_source.py.txt')})


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('folder',type=Path);p.add_argument('--encoding',action='store_true');p.add_argument('--out',type=Path,required=True);a=p.parse_args()
    from workspace_budget import reserve
    reserve(ROOT,1000000);r=encoding(a.folder) if a.encoding else discovery(a.folder)
    with a.out.open('x') as f:json.dump(r,f,indent=2)
    print(r['status'],len(r.get('records',[])) or r.get('cases'))
