"""Native FSE CLS feature predicates, pending primitive-to-attribute mapping."""
import argparse,hashlib,json
from pathlib import Path
import numpy as np
from ownpr_image_storage import sha as artifact_sha
from ownpr_fuse_audit import SwitchChoices
from ownpr_device import Device,DLL_SHA
from ownpr_lut_configuration import NativeFrameProjection
from audit_native_encoding_probe import payload


class ClsChoices:
    def __init__(self,device):
        self.device=device;self.fuses=SwitchChoices(device)
        self.features=list(device.fse.read_rows(device.fse.get_table('header',12)))
        self.templates={}

    def selected(self,predicate,attributes):
        if predicate==0:return True
        index=abs(predicate)
        if index>=len(self.features):raise ValueError('Unknown CLS feature index')
        attribute,value,_=self.features[index]
        if not 0<=attribute<len(attributes):raise ValueError('CLS feature outside attribute vector')
        match=attributes[attribute]==value
        return match if predicate>0 else not match

    def requirements(self,row,column,group,attributes):
        if group not in range(4) or len(attributes)!=19:raise ValueError('Invalid CLS group or attribute vector')
        typ=self.device.grid[row][column];key=typ,group
        if key not in self.templates:
            table=self.device.fse.get_table(str(typ),0x19+group)
            if table is None:raise ValueError('Missing native CLS feature table')
            self.templates[key]=[(record[:2],tuple(self.fuses.bit(typ,i) for i in record[2:] if i>=0)) for record in self.device.fse.read_rows(table)]
        mask=set();ones=set()
        for predicates,bits in self.templates[key]:
            mask.update(bits)
            # Native RVA 0x50a8ad returns no selections when both primary
            # mode attributes are zero, even if negative predicates match.
            if (attributes[0] or attributes[1]) and all(self.selected(p,attributes) for p in predicates):ones.update(bits)
        return {(row,column,br,bc):int((br,bc) in ones) for br,bc in mask}


def audit_sweep(folder):
    folder=Path(folder);meta=json.loads((folder/'cls_mode_sweep.json').read_text());device=Device()
    if meta['dll_sha256']!=DLL_SHA or meta['source_fse_sha256']!=device.fse.sha256 or meta['open_chip_database_used'] is not False:
        raise ValueError('Unreviewed native CLS provenance')
    def verify(file,digest):
        if artifact_sha(file)!=digest:raise ValueError('CLS capture changed')
    verify(folder/'encoding_baseline.fs',meta['baseline_sha256']);verify(folder/'encoding_restored.fs',meta['restored_sha256'])
    if payload(folder/'encoding_baseline.fs')!=payload(folder/'encoding_restored.fs'):raise ValueError('CLS baseline was not restored')
    choices=ClsChoices(device);projection=NativeFrameProjection(device);covered=set();available=set()
    def requirements(vectors):
        if len(vectors)!=len(meta['groups']):raise ValueError('Incomplete CLS vector set')
        result={}
        for record,attributes in zip(meta['groups'],vectors):
            r,c=record['native_site'];r-=1;c-=1;group=record['group'];typ=device.grid[r][c]
            for bit,value in choices.requirements(r,c,group,attributes).items():
                index=projection.index(bit);result[index]=result.get(index,0)|value
            for i,(predicates,_) in enumerate(choices.templates[typ,group]):
                key=typ,group,i;available.add(key)
                if (attributes[0] or attributes[1]) and all(choices.selected(p,attributes) for p in predicates):covered.add(key)
        return result
    base_requirements=requirements([r['attributes'] for r in meta['groups']]);baseline=projection.read(folder/'encoding_baseline.fs')
    baseline_mismatches=[i for i,v in base_requirements.items() if int(baseline[i])-48!=v]
    checks=[]
    for capture in meta['captures']:
        path=folder/capture['file'];verify(path,capture['sha256']);variant=projection.read(path)
        req=requirements(capture.get('encoded_attributes',capture['attributes']))
        actual=set(np.flatnonzero(baseline!=variant).tolist());expected={i for i,v in req.items() if v!=base_requirements[i]}
        checks.append(dict(file=path.name,expected_data_bits=len(expected),observed_data_bits=len(actual),missing=len(expected-actual),
            unexpected=len(actual-expected),match=actual==expected,rewritten_attributes=capture.get('rewritten_attributes'),
            native_attributes_observed_after_encoding='encoded_attributes' in capture))
    passed=not baseline_mismatches and all(row['match'] for row in checks)
    return dict(status='verified_sample_cls_predicates' if passed else 'failed',groups=len(meta['groups']),checks=checks,
        baseline_mismatch_count=len(baseline_mismatches),baseline_mismatch_indices=baseline_mismatches[:30],
        source_fse_sha256=device.fse.sha256,selected_row_coverage=len(covered),total_rows=len(available),unselected_rows=sorted(available-covered),
        primitive_attribute_semantics_verified=False,physical_legality_verified=False,full_bitstream_verified=False,
        scope='Native CLS raw-attribute predicates and selected configuration bits only; random attribute vectors do not establish legal FF/LUT modes.')


if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('folder',type=Path);a=ap.parse_args();result=audit_sweep(a.folder)
    (a.folder/'cls_mode_audit.json').write_text(json.dumps(result,indent=2));print(json.dumps(result,indent=2))
    if result['status']=='failed':raise SystemExit(1)
