"""Audit mux binding differential and occupied-LUT CLS requirements."""
import argparse,json
from pathlib import Path
import numpy as np
from ownpr_device import Device,DLL_SHA
from ownpr_lut_configuration import NativeFrameProjection,LutInitChoices
from ownpr_cls_configuration import ClsChoices
from audit_native_encoding_probe import payload
from ownpr_place import sha


def audit(folder):
    folder=Path(folder);meta=json.loads((folder/'manifest.json').read_text());file=folder/'mux_encoding_probe.json'
    if meta['status']!='captured' or meta['dll_sha256']!=DLL_SHA or meta['vendor_pnr_executed'] or meta['open_chip_database_used'] or sha(file)!=meta['capture_sha256']:raise ValueError('Unverified mux encoding capture')
    d=Device();image=NativeFrameProjection(d);lut=LutInitChoices(d);cls=ClsChoices(d);records=[];seen=set()
    for row in json.loads(file.read_text())['records']:
        level=row['level'];r,c=row['site'];r-=1;c-=1
        if level not in (5,6,7) or level in seen or d.grid[r][c]!=17:raise ValueError('Unsupported mux encoding case')
        seen.add(level);before=image.read(folder/row['baseline']);after=image.read(folder/row['variant']);inactive=image.read(folder/row['inactive_leaf'])
        if payload(folder/row['baseline'])!=payload(folder/row['restored']) or not row['full_payload_restored']:raise ValueError('Mux binding did not restore full payload')
        changes=np.flatnonzero(before!=after).tolist()
        if changes!=[x['index'] for x in row['changes']]:raise ValueError('Mux encoding delta mismatch')
        if changes:raise ValueError('Dedicated mux has unmodeled additional configuration')
        requirements={};active_groups={leaf['slot']//2 for leaf in row['leaf_words']}
        if len(row['leaf_words'])!=1<<(level-4) or len(row['muxes'])!=(1<<(level-4))-1:raise ValueError('Incomplete mux tree binding')
        for leaf in row['leaf_words']:requirements.update(lut.requirements(r,c,leaf['slot'],leaf['word']))
        expected_activation=set()
        for group,attrs in enumerate(row['encoded_cls']):
            if attrs[0]!=(9 if group in active_groups else 0) or attrs!=row['baseline_cls'][group]:raise ValueError('Wrong occupied-LUT CLS mode')
            bits=cls.requirements(r,c,group,attrs);requirements.update(bits);zero=list(attrs);zero[0]=0
            off=cls.requirements(r,c,group,zero)
            expected_activation.update(image.index(bit) for bit in bits if bits[bit]!=off[bit])
        mismatches=[bit for bit,value in requirements.items() if before[image.index(bit)]-48!=value or after[image.index(bit)]-48!=value]
        if mismatches:raise ValueError('Wide leaf truth or CLS bits do not match native encoding')
        activation=set(np.flatnonzero(inactive!=before).tolist())
        if activation!=expected_activation or len(activation)!=3*len(active_groups):raise ValueError('Unexplained occupied-CLS activation changes')
        records.append(dict(level=level,site=[r,c],leaf_luts=len(row['leaf_words']),muxes=len(row['muxes']),required_bits=len(requirements),
            extra_mux_binding_bits=0,cls_default_activation_bits=len(activation),full_payload_restored=True))
    if seen!={5,6,7}:raise ValueError('Incomplete dedicated mux encoding levels')
    return dict(status='isolated_wide_mux_encoding_matched',records=records,capture_sha256=meta['capture_sha256'],source_fse_sha256=d.fse.sha256,
        vendor_pnr_executed=False,full_bitstream_verified=False,
        scope='Bound leaf truth/CLS and zero additional mux-binding delta in isolated type-17 LUT5/6/7 trees. Select/output PIPs and final combined design remain separate gates.')


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('folder',type=Path);a=p.parse_args();r=audit(a.folder);(a.folder/'mux_encoding_audit.json').write_text(json.dumps(r,indent=2));print(json.dumps(r,indent=2))
