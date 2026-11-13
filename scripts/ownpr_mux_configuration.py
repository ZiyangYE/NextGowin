"""CLS requirements for isolated type-17 dedicated wide-LUT macros."""
import json
from ownpr_device import ROOT
from ownpr_place import sha
from ownpr_mux_macros import validate


def compile_wide(design,placement,device):
    folder=ROOT/'results/native_mux_encoding_003';meta=json.loads((folder/'manifest.json').read_text());audit=json.loads((folder/'mux_encoding_audit.json').read_text());data=json.loads((folder/'mux_encoding_probe.json').read_text())
    if (audit['status']!='isolated_wide_mux_encoding_matched' or audit['capture_sha256']!=meta['capture_sha256'] or sha(folder/'mux_encoding_probe.json')!=audit['capture_sha256']
        or audit['source_fse_sha256']!=device.fse.sha256 or any(r['extra_mux_binding_bits'] or not r['full_payload_restored'] for r in audit['records'])):raise ValueError('Unverified wide mux configuration calibration')
    check=validate(design,placement,device);sites=[];base=[list(r) for r in data['records'][0]['baseline_cls']]
    for row in check['macros']:
        r,c=row['row'],row['column'];leaf_slots=[v['slot'] for v in placement['clusters'].values() if (v['row'],v['column'])==(r,c)];groups=[list(a) for a in base]
        for i,a in enumerate(groups):a[0]=9 if i in {s//2 for s in leaf_slots} else 0
        for pair in check.get('wide_ff_pairs',[]):
            if pair['root']==row['root']:groups=pair['groups']
        sites.append(dict(row=r,column=c,groups=groups,root=row['root'],level=row['level']))
    return dict(sites=sites,capacity=check,calibration_sha256=audit['capture_sha256'],additional_mux_mode_bits=0,full_bitstream_verified=False)
