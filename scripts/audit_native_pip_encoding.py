"""Audit native raw PIP writes, without claiming full route-state encoding."""
import argparse,hashlib,json
from pathlib import Path
from ownpr_device import Device,DLL_SHA
from ownpr_fuse_audit import SwitchChoices
from ownpr_image_storage import load_json,sha


def audit(folder,choices=None):
    folder=Path(folder);path=folder/'native_pip_writes.json';manifest=json.loads((folder/'manifest.json').read_text())
    if (manifest.get('status')!='captured' or manifest.get('dll_sha256')!=DLL_SHA or manifest.get('open_chip_database_used') is not False
            or sha(path)!=manifest.get('pip_writes_sha256')):
        raise ValueError('Unverified native PIP capture')
    if manifest.get('vendor_placement_executed') or manifest.get('vendor_routing_executed'):
        if manifest.get('own_pnr_result') is not False or not manifest.get('fixed_cst_sha256') or not manifest.get('netlist_sha256'):
            raise ValueError('Native P&R calibration lacks explicit role and fixed-pin provenance')
    data=load_json(path);choices=choices or SwitchChoices(Device());device=choices.device
    if data['source_fse_sha256']!=device.fse.sha256:raise ValueError('Native PIP source version mismatch')
    seen=set();mismatches=[];ones=0
    for record in data['records']:
        typ,src,dst=record['tile_type'],record['source'],record['destination'];key=(typ,src,dst)
        if key in seen:raise ValueError('Duplicate PIP calibration case')
        seen.add(key);name_key=device.names[src],device.names[dst]
        predicted={bit for bit,value in choices.template(typ)[name_key].items() if value}
        observed={tuple(bit) for bit in record['set_bits']};ones+=len(observed)
        if predicted!=observed or record['clear_bits']:
            mismatches.append(dict(key=key,missing=sorted(predicted-observed),unexpected=sorted(observed-predicted),native_clears=record['clear_bits']))
    used={tuple(pip) for pip in data['used_pips']};coverage=used<=seen
    implicit=sum(bool(r.get('implicit')) for r in data['records'])
    return dict(status='verified_raw_pip_writes' if coverage and not mismatches else 'failed',connections=len(seen),implicit_connections=implicit,
        set_bit_observations=ones,mismatch_count=len(mismatches),mismatches=mismatches[:16],tile_types=sorted({key[0] for key in seen}),
        used_design_pips=len(data['used_pips']),used_design_unique_choices=len(used),used_design_coverage_complete=coverage,
        source_routes_sha256=data['used_routes_sha256'],source_fse_sha256=device.fse.sha256,source_capture_sha256=manifest['pip_writes_sha256'],
        dll_sha256=DLL_SHA,
        calibrated_keys=sorted(seen),logical_node_mapping_verified=False,default_configuration_verified=False,
        full_bitstream_verified=False,scope='Raw literal FSE PIP selected-bit writes and polarity only. High-level node mapping, defaults and composed route-state encoding remain unverified.')


if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('folder',type=Path);a=ap.parse_args();r=audit(a.folder)
    (a.folder/'pip_encoding_audit.json').write_text(json.dumps(r,separators=(',',':')))
    print(json.dumps({k:v for k,v in r.items() if k not in ('calibrated_keys','mismatches')},indent=2))
    if r['status']=='failed':raise SystemExit(1)
