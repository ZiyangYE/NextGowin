"""Move an unpaired ordinary LUT to a free slot in its current calibrated CFU.

No signal-name rules, no new FF sharing, no changes to clocks, pads or logic.
Actual routing and full native STA must decide whether a candidate is useful.
"""
import argparse,copy,json,re,shutil
from pathlib import Path
from ownpr_device import Device,ROOT
from ownpr_place import sha
from ownpr_mux_macros import validate
from ownpr_ff_configuration import compile_initial
from workspace_budget import reserve


def move(design,placed,device,name,slot):
    validate(design,placed,device);compile_initial(design,placed)
    row=placed['clusters'].get(name)
    if row is None or row.get('ff') or row.get('lut')!=name:
        raise ValueError('Slot move requires an unpaired ordinary LUT')
    if type(slot) is not int or slot not in range(8) or slot==row['slot']:
        raise ValueError('Slot must be a different integer in 0..7')
    if design['modules']['top']['cells'][name]['type'] not in ('LUT1','LUT2','LUT3','LUT4'):
        raise ValueError('Unsupported LUT primitive')
    tile=(row['row'],row['column'])
    excluded={(x['row'],x['column']) for kind in ('arithmetic_clusters','mux_clusters') for x in placed.get(kind,{}).values()}
    if tile in excluded:raise ValueError('Slot move overlaps an exclusive macro')
    capture=ROOT/'results/native_device_lut_004/lut_encoding_audit.json';cal=json.loads(capture.read_text())
    if cal['source_fse_sha256']!=device.fse.sha256 or cal['status']!='verified_sample_lut_init_mapping' or not all(r['match'] for r in cal['checks']):
        raise ValueError('LUT calibration differs from device')
    if not (0<=tile[0]<device.rows and 0<=tile[1]<device.cols) or device.grid[tile[0]][tile[1]] not in cal['tile_types']:
        raise ValueError('Uncalibrated LUT tile')
    sites=[(c['row'],c['column'],c['slot']) for c in placed['clusters'].values()]
    if len(set(sites))!=len(sites) or (*tile,slot) in sites:raise ValueError('Occupied or overlapping LUT slot')
    result=copy.deepcopy(placed);result['clusters'][name]['slot']=slot
    validate(design,result,device);compile_initial(design,result)
    result.pop('weighted_internal_manhattan',None)
    change=dict(instance=name,old_site=[*tile,row['slot']],new_site=[*tile,slot],logical_netlist_byte_identical=True,
        clock_sink_sites_preserved=True,fixed_io_preserved=True,calibration_sha256=sha(capture))
    result.update(lut_slot_move=change,capacity_legal=True,physical_legality_complete=False,timing_signoff=False)
    return result,change


def candidates(design,placed,report,mapping):
    """Rank by worst setup path involving a LUT, then its outgoing wire delay.

    Enumerate all free same-CFU slots; geometric distance cannot distinguish
    their switches. Only routed trials may claim a timing improvement.
    """
    reverse={v:k for k,v in mapping['instances'].items()};scores={};slack=None;active=False;previous=None
    excluded={(x['row'],x['column']) for kind in ('arithmetic_clusters','mux_clusters') for x in placed.get(kind,{}).values()}
    for line in report.splitlines():
        if re.match(r'^3\.\d+\.',line):active=bool(re.match(r'^3\.3\.1 Setup Analysis Report',line))
        if not active:continue
        m=re.match(r'^Slack\s*:\s*([-\d.]+)',line)
        if m:slack=float(m[1]);previous=None
        if 'Data Required Path:' in line:previous=None;slack=None
        if slack is None:continue
        m=re.match(r'^\s*[-\d.]+\s+([-\d.]+)\s+(t\w+)\s+.*\s+(own_i_\d+)/(\S+)\s*$',line)
        if not m:continue
        name=reverse.get(m[3]);row=placed['clusters'].get(name)
        if m[2]=='tNET':
            if previous in scores:scores[previous][1]=max(scores[previous][1],float(m[1]))
            previous=None
        if row and not row.get('ff') and (row['row'],row['column']) not in excluded:
            old=scores.setdefault(name,[slack,0.]);old[0]=min(old[0],slack)
        if m[2]=='tINS':previous=name if m[4]=='F' and name in scores else None
    occupied={(r['row'],r['column'],r['slot']) for r in placed['clusters'].values()};rows=[]
    for name,(slack,delay) in sorted(scores.items(),key=lambda x:(x[1][0],-x[1][1],x[0])):
        c=placed['clusters'][name]
        for slot in range(8):
            if (c['row'],c['column'],slot) not in occupied:
                rows.append(dict(instance=name,slot=slot,worst_setup_slack_ns=slack,outgoing_wire_ns=delay))
    return rows


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--base',type=Path,required=True);p.add_argument('--lut-slot',nargs=2,required=True);p.add_argument('--out',type=Path,required=True);a=p.parse_args()
    reserve(ROOT,10_000_000);design=json.loads((a.base/'packed.json').read_text());placed=json.loads((a.base/'placement.json').read_text())
    updated,change=move(design,placed,Device(),a.lut_slot[0],int(a.lut_slot[1]));a.out.mkdir(exist_ok=False)
    shutil.copyfile(a.base/'packed.json',a.out/'packed.json');(a.out/'placement.json').write_text(json.dumps(updated,indent=2))
    meta=json.loads((a.base/'manifest.json').read_text());meta.update(status='placed',netlist_sha256=sha(a.out/'packed.json'),packed_sha256=sha(a.out/'packed.json'),
        placement_sha256=sha(a.out/'placement.json'),source_placement_sha256=sha(a.base/'placement.json'),lut_slot_move=change,physical_legality_complete=False,timing_signoff=False)
    (a.out/'manifest.json').write_text(json.dumps(meta,indent=2));print(json.dumps(change,indent=2))
