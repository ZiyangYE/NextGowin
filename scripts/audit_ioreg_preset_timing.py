"""Independent DFFPE I/O timing check, including always-enabled CE.

This calibrates the cell-query model. It does not open production packing or
certify constant-control routing, initialization, final encoding or hardware.
"""
import argparse,json
from pathlib import Path
from ownpr_device import DLL_SHA
from ownpr_place import sha
from ownpr_ioreg_timing import IoregTimingModel
from ownpr_ioreg_configuration import audit_attributes


def check(data,model):
    sites={(110,135,0),(110,144,0),(110,144,1),(109,183,0),(103,183,1)};seen=set();vectors=0
    for row in data['registers']:
        site=tuple(row['io_site']);mode=row['ce_mode'];key=site,mode
        if key in seen or site not in sites or mode not in ('signal','one') or row['site']!=[0,site[0],site[1],1] or not row['clock_positive']:
            raise ValueError('Unexpected/duplicate DFFPE timing case')
        seen.add(key);queries=set()
        for q in row['queries']:
            if q['pin'] not in ('D','CE','PRESET','Q') or type(q['maximum']) is not bool or type(q['selector']) is not int:raise ValueError('Invalid DFFPE timing query label')
            key=('CLEAR' if q['pin']=='PRESET' else q['pin'],q['selector'],q['maximum'])
            if key in queries or key not in model.values or q['values']!=model.values[key]:raise ValueError('DFFPE query differs from independent raw library')
            queries.add(key);vectors+=1
        if queries!={k for k in model.values if k[0] in ('D','CE','CLEAR','Q')}:raise ValueError('Incomplete DFFPE timing query vectors')
    if seen!={(s,m) for s in sites for m in ('signal','one')}:raise ValueError('Incomplete DFFPE site/CE coverage')
    return dict(cases=len(seen),vectors=vectors,raw_library_matched=True,ce_modes=['signal','one'],clock='positive',async_control='positive PRESET')


def audit(folder):
    folder=Path(folder);meta=json.loads((folder/'manifest.json').read_text());file=folder/'ioreg_timing_queries.json'
    if (meta['status']!='captured' or meta['dll_sha256']!=DLL_SHA or meta['timing_primitive']!='DFFPE'
        or meta['vendor_placement_executed'] or meta['vendor_routing_executed'] or sha(file)!=meta['timing_sha256']
        or sha(folder/'probe.vg')!=meta['source_sha256']):raise ValueError('Unverified DFFPE calibration capture')
    attributes=audit_attributes(folder);model=IoregTimingModel();timing=check(json.loads(file.read_text()),model)
    return dict(status='isolated_preset_cell_timing_verified',attributes=attributes,timing=timing,timing_sha256=sha(file),
        source_netlist_sha256=sha(folder/'probe.vg'),clear_calibration_sha256=model.sha256,
        fixture_byte_identical_to_historical=meta['fixture_byte_identical_to_historical'],
        integrated_packing_routing_verified=False,constant_control_electrical_mode_verified=False,
        timing_signoff=False,full_bitstream_verified=False)


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('folder',type=Path);a=p.parse_args();r=audit(a.folder)
    (a.folder/'independent_timing_audit.json').write_text(json.dumps(r,indent=2));print(json.dumps(r,indent=2))
