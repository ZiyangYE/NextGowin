"""Native FSE LUT truth-bit mapping and native encoder differential audit.

This encodes LUT INIT choices only, not cell modes, clocks, bank defaults,
frame integrity words, or a complete hardware-valid bitstream.
"""
import argparse,gzip,hashlib,json
from pathlib import Path
import numpy as np
from ownpr_image_storage import sha as artifact_sha
from ownpr_device import Device,DLL_SHA
from ownpr_fuse_audit import SwitchChoices


class LutInitChoices:
    def __init__(self,device):
        self.device=device;self.fuses=SwitchChoices(device);self.templates={}

    def template(self,typ):
        if typ not in self.templates:
            table=self.device.fse.get_table(str(typ),5)
            if table is None:raise ValueError('Native LUT table missing')
            result={}
            for row in self.device.fse.read_rows(table):
                slot,bit=row[:2];key=(slot,bit)
                if not 0<=slot<8 or not 0<=bit<16 or key in result:raise ValueError('Invalid native LUT table keys')
                result[key]=tuple(self.fuses.bit(typ,i) for i in row[2:] if i>=0)
                if not result[key]:raise ValueError('Missing LUT truth-bit encoding')
            if len(result)!=128:raise ValueError('Incomplete LUT truth-bit table')
            self.templates[typ]=result
        return self.templates[typ]

    def requirements(self,row,column,slot,init16):
        if not 0<=slot<8 or not 0<=init16<=0xffff:raise ValueError('Invalid LUT slot or INIT')
        if not 0<=row<self.device.rows or not 0<=column<self.device.cols:raise ValueError('LUT tile outside device')
        mapping=self.template(self.device.grid[row][column]);result={}
        for bit in range(16):
            # Native table-5 handler selects fuse rows when INIT bit is zero.
            value=1-((init16>>bit)&1)
            for br,bc in mapping[slot,bit]:
                key=(row,column,br,bc)
                if key in result and result[key]!=value:raise ValueError('Conflicting LUT INIT fuses')
                result[key]=value
        return result


class NativeFrameProjection:
    """Projection hypothesis tested against native differential captures.

FSE tile dimensions determine global bitmap coordinates. This SDK writes one
frame per bitmap column, with rows reversed and byte-aligned padding. The
remaining frame bits are deliberately excluded: their integrity algorithm
must be independently recovered before any full bitstream writer exists.
"""
    def __init__(self,device):
        self.device=device;g=device.grid;b=device.fse.blocks
        heights=[{b[str(t)]['height'] for t in row} for row in g]
        widths=[{b[str(g[r][c])]['width'] for r in range(device.rows)} for c in range(device.cols)]
        if any(len(s)!=1 for s in heights+widths):raise ValueError('Nonrectangular native tile dimensions')
        self.ys=np.cumsum([0]+[next(iter(s)) for s in heights]).tolist()
        self.xs=np.cumsum([0]+[next(iter(s)) for s in widths]).tolist()
        self.height=self.ys[-1];self.width=self.xs[-1];self.data_width=(self.height+7)//8*8

    def index(self,bit):
        r,c,y,x=bit
        return (self.xs[c]+x)*self.data_width+self.data_width-1-(self.ys[r]+y)

    def read(self,path):
        from ownpr_image_storage import resolve
        path=resolve(path)
        opener=gzip.open if path.suffix=='.gz' else open
        with opener(path,'rt') as stream:lines=stream.read().splitlines()
        frame_width=self.data_width+64
        located=[(i,line) for i,line in enumerate(lines) if len(line)==frame_width and set(line)<={'0','1'}]
        if len(located)!=self.width or any(b[0]!=a[0]+1 for a,b in zip(located,located[1:])):
            raise ValueError('Unrecognized native frame layout')
        data=''.join(line[:self.data_width] for _,line in located).encode('ascii')
        return np.frombuffer(data,dtype=np.uint8)


def audit_sweep(folder):
    from audit_native_encoding_probe import payload
    folder=Path(folder);meta=json.loads((folder/'lut_init_sweep.json').read_text())
    if meta.get('dll_sha256')!=DLL_SHA or meta.get('open_chip_database_used') is not False:
        raise ValueError('Unreviewed LUT calibration provenance')
    def verify(path,digest):
        if artifact_sha(path)!=digest:raise ValueError('Calibration artifact changed')
    verify(folder/'encoding_baseline.fs',meta['baseline_sha256']);verify(folder/'encoding_restored.fs',meta['restored_sha256'])
    if payload(folder/'encoding_baseline.fs')!=payload(folder/'encoding_restored.fs'):
        raise ValueError('Complete native payload was not restored')
    d=Device();lut=LutInitChoices(d);projection=NativeFrameProjection(d)
    baseline=projection.read(folder/'encoding_baseline.fs');restored=projection.read(folder/'encoding_restored.fs')
    if not np.array_equal(baseline,restored):raise ValueError('LUT calibration baseline was not restored')
    targets=[];all_bits=set();kinds=set();slots=set()
    for target in meta['targets']:
        kind,r,c,slot=target['physical_header'];r-=1;c-=1
        if kind!=24:raise ValueError('Expected physical LUT4')
        requirements=lut.requirements(r,c,slot,target['original_init'])
        for bit,value in requirements.items():
            index=projection.index(bit)
            if index in all_bits:raise ValueError('Overlapping calibration LUTs')
            all_bits.add(index)
            if int(baseline[index])-48!=value:raise ValueError('Native baseline disagrees with LUT truth-bit polarity or mapping')
        targets.append((r,c,slot,target['original_init'],requirements));kinds.add(d.grid[r][c]);slots.add(slot)
    checks=[]
    for capture in meta['captures']:
        path=folder/capture['file'];verify(path,capture['sha256']);variant=projection.read(path)
        actual=set(np.flatnonzero(baseline!=variant).tolist());expected=set()
        for r,c,slot,init,requirements in targets:
            changed=lut.requirements(r,c,slot,init^capture['mask'])
            expected.update(projection.index(bit) for bit,v in changed.items() if v!=requirements[bit])
        checks.append(dict(mask=capture['mask'],expected_data_bits=len(expected),observed_data_bits=len(actual),
                           missing=len(expected-actual),unexpected=len(actual-expected),match=actual==expected))
    masks={c['mask'] for c in checks}
    complete=masks=={0xffff,0xaaaa,0xcccc,0xf0f0,0xff00} and slots==set(range(8))
    passed=complete and all(c['match'] for c in checks)
    return dict(status='verified_sample_lut_init_mapping' if passed else 'failed',targets=len(targets),slots=sorted(slots),tile_types=sorted(kinds),
        data_bits_covered=len(all_bits),checks=checks,complete_bit_index_patterns=complete,source_fse_sha256=d.fse.sha256,
        bitmap_height=projection.height,bitmap_width=projection.width,baseline_restored=True,
        full_bitstream_verified=False,primitive_modes_verified=False,
        scope='LUT INIT encoding and frame-data projection verified at the sampled native placements only; mode/default/routing/clock and frame-integrity encoding remain separate obligations.')


if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('folder',type=Path);a=ap.parse_args()
    result=audit_sweep(a.folder);(a.folder/'lut_encoding_audit.json').write_text(json.dumps(result,indent=2));print(json.dumps(result,indent=2))
    if result['status']=='failed':raise SystemExit(1)
