"""Bind historical route validation to immutable source netlist/placement bytes."""
import json
from pathlib import Path
from ownpr_image_storage import load_json,sha,available,copy_original


def stage(source,target_placement,out,explicit=None):
    source=Path(source).resolve();out=Path(out);file=source/'manifest.json';meta=load_json(file);digest=sha(file)
    if meta['status']!='routed' or meta['vendor_pnr_executed']:raise ValueError('Source must be completed own routing')
    candidates=[Path(explicit).resolve()] if explicit is not None else [source/'placement']+([Path(meta['placement_source'])] if meta.get('placement_source') else [])+[Path(target_placement).resolve()]
    selected=None
    for folder in candidates:
        files=[folder/'packed.json',folder/'placement.json']
        if all(available(f) for f in files) and sha(files[0])==meta['netlist_sha256'] and sha(files[1])==meta['placement_sha256']:
            selected=folder;break
    if selected is None:raise ValueError('Exact source placement unavailable; supply --reuse-placement')
    context=out/'reuse_source';context.mkdir(exist_ok=False)
    for name in ('packed.json','placement.json'):copy_original(selected/name,context/name)
    record=dict(status='source_configuration_hash_bound',source_placement=str(selected.resolve()),source_routes=str(source),source_manifest_sha256=digest,
                packed_sha256=sha(context/'packed.json'),placement_sha256=sha(context/'placement.json'))
    if not all(available(source/name) for name in ('nets.json','routes.json')):raise ValueError('Missing source routing inputs')
    record['route_input_sha256']={name:sha(source/name) for name in ('nets.json','routes.json')}
    if record['packed_sha256']!=meta['netlist_sha256'] or record['placement_sha256']!=meta['placement_sha256'] or sha(file)!=digest:raise ValueError('Source configuration changed during snapshot')
    (context/'manifest.json').write_text(json.dumps(record,indent=2));return record


def load(out,source):
    context=Path(out)/'reuse_source';record=load_json(context/'manifest.json');meta=load_json(Path(source)/'manifest.json')
    if record['status']!='source_configuration_hash_bound' or record['source_manifest_sha256']!=sha(Path(source)/'manifest.json'):raise ValueError('Source route configuration provenance changed')
    if sha(context/'packed.json')!=record['packed_sha256'] or sha(context/'placement.json')!=record['placement_sha256'] or record['packed_sha256']!=meta['netlist_sha256'] or record['placement_sha256']!=meta['placement_sha256']:raise ValueError('Source configuration snapshot changed')
    for name,h in record.get('route_input_sha256',{}).items():
        if sha(Path(source)/name)!=h:raise ValueError('Source route inputs changed after staging')
    return load_json(context/'packed.json'),load_json(context/'placement.json'),record
