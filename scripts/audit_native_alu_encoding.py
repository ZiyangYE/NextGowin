"""Audit initial ALU mode-2 constant folding and CLS configuration evidence."""
import argparse,hashlib,json,re
from pathlib import Path
import numpy as np
from ownpr_device import Device,DLL_SHA,ROOT
from ownpr_lut_configuration import NativeFrameProjection,LutInitChoices
from ownpr_cls_configuration import ClsChoices
from audit_native_encoding_probe import payload


def audit(folder,reference=ROOT/'results/native_uart_carry_003'):
    folder=Path(folder);reference=Path(reference);meta=json.loads((folder/'manifest.json').read_text());data=json.loads((folder/'alu_encoding_probe.json').read_text())
    if meta['status']!='captured' or meta['dll_sha256']!=DLL_SHA or meta['vendor_pnr_executed'] or meta['open_chip_database_used']:raise ValueError('Unverified ALU encoding probe')
    device=Device();projection=NativeFrameProjection(device);luts=LutInitChoices(device);cls=ClsChoices(device)
    base=projection.read(folder/data['baseline'])
    if payload(folder/'probe.fs')!=payload(folder/data['baseline']):raise ValueError('Complete ALU baseline did not restore')
    profiles={};records=[]
    for row in data['records']:
        if row['mode']!=2 or row['slot'] not in range(6):raise ValueError('Uncalibrated ALU function or slot')
        image=projection.read(folder/row['variant']);indices=np.flatnonzero(base!=image).tolist()
        if not indices or indices!=[r['index'] for r in row['changes']] or not row['full_payload_restored']:raise ValueError('Empty or inconsistent ALU encoding differential')
        for change in row['changes']:
            if int(image[change['index']])-48!=change['after'] or int(base[change['index']])-48!=change['before']:raise ValueError('ALU differential values changed')
        r,c=row['native_site'];r-=1;c-=1;slot=row['slot'];word=0
        for bit,positions in [(i,luts.template(row['tile_type'])[slot,i]) for i in range(16)]:
            values={int(image[projection.index((r,c,*pos))])-48 for pos in positions}
            if len(values)!=1:raise ValueError('Conflicting ALU truth fuses')
            word|=(1-values.pop())<<bit
        for group,attributes in enumerate(row['cls_attributes']):
            req=cls.requirements(r,c,group,attributes)
            if any(int(image[projection.index(b)])-48!=v for b,v in req.items()):raise ValueError('ALU CLS attribute encoding mismatch')
        key=tuple(row.get('input_profile',['signal']*4))
        if key in profiles and profiles[key]!=word:raise ValueError('ALU truth word depends on unsupported site/slot condition')
        profiles[key]=word;records.append(dict(slot=slot,profile=list(key),truth_word=word,changed_bits=len(indices)))
    refmeta=json.loads((reference/'manifest.json').read_text());refpath=reference/'primitive_bindings.json'
    if (refmeta['status']!='captured' or refmeta['own_pnr_result'] is not False or refmeta['dll_sha256']!=DLL_SHA
            or hashlib.sha256(refpath.read_bytes()).hexdigest()!=refmeta['bindings_sha256']):raise ValueError('Unverified native ALU reference')
    text=(reference/'probe.vg').read_text();constants={}
    for kind,body in re.findall(r'\b(VCC|GND)\s+[^\s(]+\s*\((.*?)\);',text,re.S):
        match=re.search(r'\.[VG]\s*\((.*?)\)',body)
        if not match:raise ValueError('Malformed constant primitive in ALU reference')
        signal=match[1].strip().lstrip('\\');constants[signal]=int(kind=='VCC')
    refimage=projection.read(reference/'probe.fs');matched=0;uncovered=[]
    for row in json.loads(refpath.read_text()):
        if row['logical_class']!='.?AVAlu@NETLIST@@' or row['alu_init']!='2':continue
        byindex={p['index']:p for p in row['pins']};key=tuple(constants.get(byindex[i]['net'],'signal') for i in range(4))
        if key not in profiles:uncovered.append(dict(instance=row['name'],profile=key));continue
        _,r,c,slot=row['physical_header'];req=luts.requirements(r-1,c-1,slot,profiles[key])
        if any(int(refimage[projection.index(b)])-48!=v for b,v in req.items()):raise ValueError('ALU truth prediction differs from engineering reference')
        if row.get('physical_alu_cached_logic_matches') is not True:raise ValueError('Native ALU typed binding was not observed')
        matched+=1
    return dict(status='initial_mode2_profiles_verified',records=records,profiles=len(profiles),reference_cells_matched=matched,reference_uncovered=uncovered,
        full_payload_restored=True,source_fse_sha256=device.fse.sha256,dll_sha256=DLL_SHA,
        capture_sha256=hashlib.sha256((folder/'alu_encoding_probe.json').read_bytes()).hexdigest(),reference_bindings_sha256=refmeta['bindings_sha256'],
        own_pnr_result=False,all_alu_modes_verified=False,carry_routing_verified=False,full_bitstream_verified=False)


if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('folder',type=Path);a=ap.parse_args();r=audit(a.folder)
    (a.folder/'alu_configuration_audit.json').write_text(json.dumps(r,indent=2));print(json.dumps(r,indent=2))
