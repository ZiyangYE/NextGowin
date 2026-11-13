"""Hash-bound independent FF selector model and ordinary-CFU admission."""
import hashlib,json
from functools import lru_cache
from pathlib import Path
from ownpr_ff_configuration import single_ff_attributes

AUDITS={'discovery_audit.json':'f84ce27bf974f219abbc5f20e126192c7d036bb3beac99ace9f11d7013ceaea1','encoding_audit_001.json':'7f58e91ac1f4959f50d88d847fa934eb375b6f3a324b66b1a1d443396a96aa25','encoding_audit_002.json':'1ae5f7b44ba298bef15aa13532a0ac41b8da30e3c03a92e97aa21bb1473a6a6f'}


@lru_cache(None)
def calibration():
    from ownpr_device import ROOT
    from ownpr_image_storage import load_json,sha
    root=ROOT/'results/independent_ff_input_trial_017';covered=set()
    for name,h in AUDITS.items():
        if sha(root/name)!=h:raise ValueError('Independent FF calibration audit changed')
        audit=load_json(root/name)
        for p,digest in audit['source_sha256'].items():
            path=Path(p);path=path if path.is_absolute() else ROOT/path
            if sha(path)!=digest:raise ValueError('Independent FF calibration source changed')
        for row in audit.get('records',[]):covered.add((row['tile_type'],row['slot']))
        for p in audit['source_sha256']:
            if not p.endswith('encoding.json'):continue
            source=Path(p);source=source if source.is_absolute() else ROOT/source
            for row in load_json(source)['records']:
                for label in ('first','stable','repeat','linked','restored'):
                    record=row[label]
                    if sha(source.parent/record['file'])!=record['compressed_sha256']:raise ValueError('Independent FF encoded witness changed')
    if covered!={(t,s) for t in (17,228) for s in range(8)}:raise ValueError('Incomplete independent FF mode coverage')
    return hashlib.sha256(json.dumps(AUDITS,sort_keys=True).encode()).hexdigest()


def validate(design,placed,device):
    marked=[r for r in placed['clusters'].values() if r.get('independent_ff_input')]
    if not marked:return []
    token=calibration()
    if placed.get('independent_ff_calibration')!=token:raise ValueError('Independent FF placement lacks calibration binding')
    cells=design['modules']['top']['cells'];records=[];seen=set()
    excluded={(p['row'],p['column']) for key in ('arithmetic_clusters','mux_clusters') for p in placed.get(key,{}).values()}
    excluded.update((p['row'],p['column']+i) for p in placed.get('hard_clusters',{}).values() for i in range(3))
    for p in marked:
        r,c,s=p['row'],p['column'],p['slot'];ff=p.get('ff');lut=p['lut']
        if ff is None or ff in seen or not 0<=r<device.rows or not 0<=c<device.cols or p.get('wide_ff_root') or (r,c) in excluded or device.grid[r][c] not in (17,228) or s not in (0,2,4):raise ValueError('Unsupported independent FF placement/sharing')
        if sum(bool(q.get('ff')) and (q['row'],q['column'],q['slot']//2)==(r,c,s//2) for q in placed['clusters'].values())!=1:raise ValueError('Independent input sharing a control group is not enabled')
        if cells[lut]['type'] not in ('LUT1','LUT2','LUT3','LUT4') or cells[ff]['connections']['D']==cells[lut]['connections']['F']:raise ValueError('Independent FF must share an unrelated ordinary LUT')
        data=cells[ff]['connections']['D']
        if len(data)!=1 or type(data[0]) is not int:raise ValueError('Independent FF requires a routed scalar data net')
        constants={b for c in cells.values() if c['type'] in ('GND','VCC') for bs in c['connections'].values() for b in bs}
        if data==cells[ff]['connections']['Q'] or data[0] in constants:raise ValueError('Direct self-feedback/constant FF data lacks complete native STA coverage')
        seen.add(ff);records.append(dict(ff=ff,lut=lut,row=r,column=c,slot=s,selector=selector(s),calibration_sha256=token))
    return records


def selector(slot,occupied_mux_slots=()):
    if type(slot) is not int or slot not in range(8):raise ValueError('Invalid independent FF slot')
    if any(type(s) is not int or s not in range(8) for s in occupied_mux_slots):raise ValueError('Invalid occupied mux slot')
    if slot in occupied_mux_slots:raise ValueError('Independent FF input conflicts with a wide-MUX selector')
    return 'SEL'+str(slot)


def groups(slot,cell_type='DFFRE',ce='one',control='zero',clock='signal'):
    selector(slot)
    result=single_ff_attributes(cell_type,slot,ce,control,clock)
    result[slot//2][11+(slot&1)]=1
    return result
