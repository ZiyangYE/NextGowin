"""Move a calibrated standalone identity buffer; retain every logical bit/cell."""
import argparse,copy,json,shutil,statistics,time
from datetime import datetime,timezone
from pathlib import Path
from ownpr_device import ROOT,Device
from ownpr_image_storage import load_json,sha
from ownpr_hold_buffers import receiver_site
from ownpr_mux_macros import validate
from ownpr_carry_macros import validate as validate_carry
from ownpr_ff_configuration import compile_initial
from ownpr_hard_architecture import HardArchitecture
from workspace_budget import reserve

def prepare(base,buffer,fraction,out):
    started=datetime.now(timezone.utc).isoformat();stamp=time.monotonic()
    if not 0<=fraction<=1:raise ValueError('Fraction outside source-to-load interval')
    reserve(ROOT,50_000_000);design=load_json(base/'packed.json');old=load_json(base/'placement.json');placed=copy.deepcopy(old)
    cells=design['modules']['top']['cells'];cell=cells[buffer];loc=placed['clusters'][buffer]
    if cell['type']!='LUT4' or cell['parameters'].get('INIT')!='1010101010101010' or loc.get('ff'):raise ValueError('Only standalone calibrated identity LUT4 supported')
    source_bit=cell['connections']['I0'][0];dest_bit=cell['connections']['F'][0]
    drivers=[n for n,c in cells.items() if c['type']=='IBUF' and c['connections'].get('O')==[source_bit]]
    if len(drivers)!=1 or drivers[0] not in placed['fixed_io']:raise ValueError('Buffer must be directly driven by one fixed input')
    anchors=[]
    for n,c in cells.items():
        for pin,bits in c['connections'].items():
            if c['port_directions'][pin]=='input' and dest_bit in bits:
                if bits!=[dest_bit] or pin in ('CLK','CLKA','CLKB'):raise ValueError('Unsupported buffer sink')
                row=receiver_site(cells,placed,n,pin);anchors.append((row['row'],row['column']))
    if not anchors:raise ValueError('Buffer has no supported sinks')
    source=placed['fixed_io'][drivers[0]]['native_ports']['out'];center=[statistics.median(v[i] for v in anchors) for i in (0,1)]
    target=[source[i]-1+fraction*(center[i]-(source[i]-1)) for i in (0,1)]
    device=Device();cal=load_json(ROOT/'results/native_device_lut_004/lut_encoding_audit.json')
    if cal['source_fse_sha256']!=device.fse.sha256 or not all(r['match'] for r in cal['checks']):raise ValueError('LUT calibration mismatch')
    occupied={(v['row'],v['column'],v['slot']) for n,v in placed['clusters'].items() if n!=buffer}
    excluded={(v['row'],v['column']) for kind in ('arithmetic_clusters','mux_clusters') for v in placed.get(kind,{}).values()}
    for v in placed.get('hard_clusters',{}).values():excluded.update((v['row'],v['column']+i) for i in range(3))
    sites=((abs(y-target[0])+abs(x-target[1]),y,x,s) for y in range(device.rows) for x in range(device.cols) if device.grid[y][x] in cal['tile_types'] and (y,x) not in excluded for s in range(8) if (y,x,s) not in occupied)
    _,r,c,s=min(sites);loc.update(row=r,column=c,slot=s)
    if loc==old['clusters'][buffer]:raise ValueError('Candidate does not move buffer')
    placed.update(physical_legality_complete=False,timing_signoff=False);placed.pop('weighted_internal_manhattan',None)
    validate(design,placed,device);validate_carry(design,placed,device);compile_initial(design,placed)
    hard=HardArchitecture().validate(design,placed,device) if placed.get('hard_clusters') else None
    out.mkdir(exist_ok=False);shutil.copyfile(base/'packed.json',out/'packed.json');(out/'placement.json').write_text(json.dumps(placed,indent=2))
    if sha(base/'packed.json')!=sha(out/'packed.json'):raise ValueError('Netlist changed during placement-only transformation')
    record=dict(status='placed',buffer=buffer,fraction=fraction,source=drivers[0],sink_count=len(anchors),old_site=old['clusters'][buffer],new_site=loc,target=target,netlist_sha256=sha(out/'packed.json'),placement_sha256=sha(out/'placement.json'),source_placement_sha256=sha(base/'placement.json'),logical_netlist_unchanged=True,clock_endpoints_unchanged=True,fixed_io_unchanged=old['fixed_io']==placed['fixed_io'],hard_resources=hard,timing_signoff=False)
    record.update(started_utc=started,wall_seconds=time.monotonic()-stamp)
    (out/'manifest.json').write_text(json.dumps(record,indent=2));return record

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--base',type=Path,required=True);p.add_argument('--buffer',required=True);p.add_argument('--fraction',type=float,required=True);p.add_argument('--out',type=Path,required=True);a=p.parse_args();print(json.dumps(prepare(a.base,a.buffer,a.fraction,a.out),indent=2))
