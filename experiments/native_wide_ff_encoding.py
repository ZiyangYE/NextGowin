"""Isolated wide-root/FF parent-link encoding and native wire-delay witness.

Called only after a complete wide tree and its real FF net have been bound.
This is calibration, not routing/STA/hardware validation of a full design.
"""
import gzip,json,shutil,struct


def capture(n,out,device,row,groups,entries):
    import ctypes as C
    import numpy as np
    from ownpr_lut_configuration import NativeFrameProjection,LutInitChoices
    from ownpr_cls_configuration import ClsChoices
    from ownpr_ff_configuration import single_ff_attributes
    from ownpr_clock_images import image_sha
    from ownpr_image_storage import sha
    from audit_native_encoding_probe import payload
    sources=row['source_data_points'];destinations=row['data_points']
    if len(sources)!=1 or len(destinations)!=1:raise ValueError('Expected scalar wide FF endpoints')
    source,destination=tuple(sources[0]),tuple(destinations[0])
    if destination not in n.fanouts(source):raise ValueError('Native wide FF fixed edge is missing')
    if row['leaf_input_conflict'] or row['selector_input_conflict']:raise ValueError('Wide FF data endpoint conflicts with its own macro')
    r,c=row['site'];slot=row['ff_slot'];level=int(row['driver']);prefix=f't{row["tile_type"]}_lut{level}'
    encode=n.fn('?generateFusemap@@YAHPEAVglobalMem@@@Z',C.c_int,[C.c_void_p])
    projection=NativeFrameProjection(device);lut=LutInitChoices(device);cls=ClsChoices(device)
    leaf_slots={entry['slot'] for entry in entries if entry['level']==4}
    attributes_before_leaf_activation=[list(struct.unpack('<19h',C.string_at(p+6,38))) for p in groups]
    # The native FF converter only activates its own group. Match the already
    # calibrated wide-LUT importer's activation of every occupied leaf group.
    for group in {s//2 for s in leaf_slots}:C.c_short.from_address(groups[group]+6).value=9
    def save(label):
        if encode(n.mem):raise ValueError('Wide FF encoder failed')
        raw=out/'probe.fs';path=out/(prefix+'_'+label+'.fs.gz')
        with raw.open('rb') as src,gzip.open(path,'xb',compresslevel=6) as dst:shutil.copyfileobj(src,dst)
        if image_sha(path)!=sha(raw):raise ValueError('Compressed wide FF encoding changed')
        return dict(file=path.name,original_sha256=sha(raw),compressed_sha256=sha(path),attributes=[list(struct.unpack('<19h',C.string_at(p+6,38))) for p in groups])
    first=save('first');warmup=save('warmup');baseline=save('unrouted')
    parents={source:None,destination:source};pointers=n.resolve_routed_nodes(parents)
    with n.resolved_parent_tree(parents,pointers):
        timing={str(selector):list(n.delay_pointer(pointers[destination],selector)) for selector in (0,1,2)}
        routed=save('linked')
    restored=save('restored')
    before=projection.read(out/baseline['file']);after=projection.read(out/routed['file'])
    actual=np.flatnonzero(before!=after).tolist()
    profile=row['ff_profile'];predicted=single_ff_attributes(slot=slot,**profile)
    for group in {s//2 for s in leaf_slots}:predicted[group][0]=9
    requirements={}
    for s in leaf_slots:requirements.update(lut.requirements(r-1,c-1,s,0xA639))
    for group,attributes in enumerate(predicted):requirements.update(cls.requirements(r-1,c-1,group,attributes))
    mismatch=[dict(bit=list(bit),expected=v,actual=int(after[projection.index(bit)])-48) for bit,v in requirements.items() if after[projection.index(bit)]-48!=v]
    recovered=payload(out/baseline['file'])==payload(out/restored['file'])
    stable=payload(out/warmup['file'])==payload(out/baseline['file'])
    attrs_match=baseline['attributes']==predicted and routed['attributes']==predicted
    result=dict(site=[r,c],tile_type=row['tile_type'],level=level,slot=slot,source=list(source),destination=list(destination),
        first=first,warmup=warmup,baseline=baseline,linked=routed,restored=restored,source_fse_sha256=device.fse.sha256,ff_profile=profile,
        stable_baseline=stable,full_payload_restored=recovered,linked_payload_equals_unrouted=payload(out/baseline['file'])==payload(out/routed['file']),
        changed_frame_bits=len(actual),frame_changes=[dict(index=i,before=int(before[i])-48,after=int(after[i])-48) for i in actual],
        predicted_attributes=predicted,attributes_match_simple_ff_plus_wide_model=attrs_match,required_bits=len(requirements),configuration_mismatches=mismatch,
        attributes_before_leaf_activation=attributes_before_leaf_activation,leaf_groups_activated=sorted({s//2 for s in leaf_slots}),
        native_wire_delay_by_selector=timing,native_edge_present=True,logical_connection_verified=True,ordinary_input_conflict=False,
        production_enabled=False,full_sta_verified=False,hardware_verified=False,
        scope='Bound same-tile wide MUX root and DFFRE with explicit CE/reset profile; reversible physical parent-link encoding and isolated wire delay only.')
    print(prefix,'frame_bits',len(actual),'attrs',attrs_match,'mismatch',len(mismatch),'restored',recovered,'timing',timing,flush=True)
    (out/(prefix+'_comparison.json')).write_text(json.dumps(result,indent=2))
    if not stable or not recovered:raise ValueError('Wide FF encoding did not stabilize/restore')
    return result
