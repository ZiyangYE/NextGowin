"""Read native encoding side effects for independent FF data; no own mode enabled."""
import gzip,json,shutil,struct

def capture(n,out,device,row,groups,entries):
    import ctypes as C
    from ownpr_image_storage import sha
    from ownpr_clock_images import image_sha
    from audit_native_encoding_probe import payload
    r,c=row['site'];slot=row['ff_slot'];mode=row['independent_input_mode'];prefix=f't{row["tile_type"]}_{mode}_s{slot}'
    encode=n.fn('?generateFusemap@@YAHPEAVglobalMem@@@Z',C.c_int,[C.c_void_p])
    def attributes():return [list(struct.unpack('<19h',C.string_at(p+6,38))) for p in groups]
    initial=attributes()
    for group in {e['slot']//2 for e in entries if e['level']==4}:C.c_short.from_address(groups[group]+6).value=9
    def save(label):
        if encode(n.mem):raise ValueError('Independent FF encoding failed')
        raw=out/'probe.fs';path=out/(prefix+'_'+label+'.fs.gz')
        with raw.open('rb') as src,gzip.open(path,'xb',compresslevel=6) as dst:shutil.copyfileobj(src,dst)
        if image_sha(path)!=sha(raw):raise ValueError('Independent FF compression changed payload')
        return dict(file=path.name,original_sha256=sha(raw),compressed_sha256=sha(path),attributes=attributes())
    first=save('first');stable=save('stable');repeat=save('repeat')
    if payload(out/stable['file'])!=payload(out/repeat['file']):raise ValueError('Native independent FF image not stable')
    raw=(r,c,295+slot);terminal=tuple(row['data_points'][0]);fanouts=n.fanouts(terminal)
    linked=None;restored=None;wire=None;parent=None
    if mode=='companion':
        edges=[e for e in device.connections(row['tile_type']) if e['destination_name']=='SEL'+str(slot)]
        if not edges:raise ValueError('Missing independent FF selector PIPs')
        e=edges[0];parent=(r,c,device.ids[e['source_name']]);parents={parent:None,terminal:parent}
        pointers=n.resolve_routed_nodes(parents)
        with n.resolved_parent_tree(parents,pointers):
            wire={str(k):list(n.delay_pointer(pointers[terminal],k)) for k in (0,1,2)}
            linked=save('linked')
        restored=save('restored')
    result=dict(tile_type=row['tile_type'],site=row['site'],slot=slot,mode=mode,pre_encoding_attributes=initial,first=first,stable=stable,repeat=repeat,linked=linked,restored=restored,parent=parent,native_wire_delay_by_selector=wire,
                logical_data_terminal=terminal,raw_physical_data=raw,terminal_to_raw_edge=raw in fanouts,
                terminal_fanouts=fanouts,production_enabled=False,full_sta_verified=False,scope='Native exclusive one-FF bindings and encoding side effects; no complete route or multi-FF legality claim')
    print(prefix,'attrs',initial[slot//2][11:13],'->',stable['attributes'][slot//2][11:13], 'edge',raw in fanouts,flush=True)
    return result
