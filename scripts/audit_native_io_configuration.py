"""Readback and differential checks for the initial fixed-I/O/GSR profile."""
import argparse,hashlib,json
from pathlib import Path
import numpy as np
from ownpr_device import Device,ROOT,DLL_SHA
from ownpr_lut_configuration import NativeFrameProjection
from ownpr_fuse_audit import SwitchChoices
from audit_native_encoding_probe import payload

REFERENCE=ROOT/'results/native_io_reference_001'
REFERENCE_STATE_SHA='94dcd3b28da224d44379d774c0608d0b0a750c33c5e339bbccc7c98c615f8abd'
def sha(p):
    from ownpr_image_storage import sha as artifact_sha
    return artifact_sha(p)


def check_state(current,reference):
    expected={tuple(r['site']):r for r in reference['io']};seen=set()
    for row in current['io']:
        key=tuple(row['site'])
        if key in seen or key not in expected:raise ValueError('Unexpected or duplicate physical I/O site')
        seen.add(key)
        if any(row[k]!=expected[key][k] for k in ('logical','physical')):raise ValueError('I/O attribute readback differs from fixed-contract reference')
    if seen!=set(expected):raise ValueError('Incomplete physical I/O coverage')
    if current['gsr']!=reference['gsr'] or not all(current['gsr'].values()):raise ValueError('GSR resource is not fully bound')
    return dict(ports=len(seen),logical_attributes=len(seen)*16,physical_attributes=len(seen)*16,gsr_bound=True)


def audit(folder):
    folder=Path(folder);manifest=json.loads((folder/'manifest.json').read_text());reference_folder=REFERENCE;reference_state_sha=REFERENCE_STATE_SHA
    if (folder/'io_reference.json').exists():
        from ownpr_io_reference import load
        if sha(folder/'io_reference.json')!=manifest.get('io_reference_sha256'):raise ValueError('I/O reference bundle changed')
        bundle=json.loads((folder/'io_reference.json').read_text());reference_folder=Path(bundle['source'])
        checked=load(reference_folder,folder/'project.cst')
        # The bundle bytes/hash remain immutable. Only its historical location
        # is normalized; every captured attribute and source digest still match.
        expected=dict(bundle,source=str(reference_folder.resolve()))
        if checked!=expected:raise ValueError('I/O reference capture changed')
        reference_state_sha=checked['state_sha256']
    refmeta=json.loads((reference_folder/'manifest.json').read_text())
    if refmeta['status']!='captured' or refmeta['dll_sha256']!=DLL_SHA or sha(reference_folder/'io_configuration_state.json')!=reference_state_sha:
        raise ValueError('Unverified I/O reference')
    if manifest['cst_sha256']!=refmeta['cst_sha256'] or manifest['vendor_placement_executed'] or manifest['vendor_routing_executed']:raise ValueError('Different I/O contract or backend')
    state=json.loads((folder/'io_state_after_encoding.json').read_text());reference=json.loads((reference_folder/'io_configuration_state.json').read_text());result=check_state(state,reference)
    file=folder/'configuration_probe.json';probe=json.loads(file.read_text()) if file.exists() else None
    # Earlier calibration captures retained the first production image under
    # encoding_initial and left probe.fs as the repeated-encoding calibration.
    legacy_probe=probe is not None and 'production' not in probe
    production_image=folder/'encoding_initial.fs.gz' if legacy_probe else folder/'probe.fs'
    device=Device();projection=NativeFrameProjection(device);image=projection.read(production_image);ref_image=projection.read(reference_folder/'probe.fs');choices=SwitchChoices(device)
    # Native table-20/60 choices at the dedicated GSR tile; both were absent
    # before GSR binding, and both agree with the completed native reference.
    gsr_bits=[]
    for table,key in ((20,(1,0)),(60,(8,0))):
        rows=[r for r in device.fse.read_rows(device.fse.get_table('220',table)) if tuple(r[:2])==key]
        if len(rows)!=1:raise ValueError('Ambiguous GSR table choice')
        for fuse in rows[0][2:]:
            if fuse<0:continue
            bit=(108,180,*choices.bit(220,fuse));index=projection.index(bit)
            if image[index]!=49 or ref_image[index]!=49:raise ValueError('GSR configuration bit differs from native reference')
            gsr_bits.append(dict(bit=bit,index=index,value=1,table=table,fuse=fuse))
    result.update(status='initial_io_state_and_gsr_bits_matched',gsr_bits=gsr_bits,production_image=str(production_image),source_sha256=dict(state=sha(folder/'io_state_after_encoding.json'),image=sha(production_image),reference_state=reference_state_sha,reference_image=sha(reference_folder/'probe.fs')),
        all_io_encoding_modes_verified=False,full_bitstream_verified=False,device_programmed=False)
    if file.exists():
        if probe['status']!='restored' or not probe['full_payload_restored']:raise ValueError('Incomplete configuration differential')
        baseline=projection.read(folder/probe['baseline']);expected_cases={(r['instance'],2) for r in state['io']}|{(r['instance'],3) for r in state['io'] if r['logical'][3]==98};seen=set();checked=0
        for record in probe['records']:
            variant=projection.read(folder/record['variant']);different=np.flatnonzero(baseline!=variant).tolist()
            claimed=[r['index'] for r in record['changes']]
            if not different or different!=claimed or not record['full_payload_restored']:raise ValueError('Nonreversible or empty attribute differential')
            for row in record['changes']:
                if int(baseline[row['index']])-48!=row['before'] or int(variant[row['index']])-48!=row['after']:raise ValueError('Differential bit mismatch')
            if record['kind']=='io':seen.add((record['instance'],record['attribute']))
            elif record['kind']=='gsr':
                if set(different)!={r['index'] for r in gsr_bits}:raise ValueError('Unexpected GSR binding bit changes')
            checked+=len(different)
        if seen!=expected_cases:raise ValueError('Incomplete I/O differential case coverage')
        restored=folder/'probe.fs' if legacy_probe else folder/probe['restored']
        if payload(restored)!=payload(folder/probe['baseline']):raise ValueError('Restored calibration payload differs from baseline')
        if not legacy_probe and payload(production_image)!=payload(folder/probe['production']):raise ValueError('Production output was replaced by a calibration image')
        defaults=np.flatnonzero(image!=baseline)
        if np.any(image[defaults]!=ref_image[defaults]):raise ValueError('Unused-pad production default differs from reference')
        result.update(status='initial_io_gsr_differentials_verified',io_differential_cases=len(seen),changed_bit_observations=checked,full_payload_restored=True,
            calibration_default_differences=len(defaults),production_defaults_match_reference=True,probe_fs_is_production=not legacy_probe)
    return result


if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('folder',type=Path);a=ap.parse_args();r=audit(a.folder)
    (a.folder/'io_configuration_audit.json').write_text(json.dumps(r,indent=2));print(json.dumps(r,indent=2))
