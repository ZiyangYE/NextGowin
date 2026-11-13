"""Timing-guided relocation of ordinary paired LUT/FF clusters.

No logic or state changes. Only free, independently controlled slots are used;
all incident data/control edges contribute to candidate cost. Clock paths must
be retargeted and revalidated by the complete implementation flow.
"""
import argparse,copy,json,re,shutil,statistics,time
from pathlib import Path
from ownpr_device import Device,ROOT
from ownpr_image_storage import load_json,read_text,sha
from ownpr_critical_chain_move import locations,exclusive_tiles
from ownpr_ff_configuration import compile_initial
from ownpr_mux_macros import validate as validate_wide
from ownpr_carry_macros import validate as validate_carry


def rank(design,placed,device,priorities,report,mapping,limit=12):
    cells=design['modules']['top']['cells'];points=locations(placed);excluded=exclusive_tiles(placed)
    paired={r['ff']:n for n,r in placed['clusters'].items() if r.get('ff') and not r.get('wide_ff_root') and (r['row'],r['column']) not in excluded}
    reverse={v:k for k,v in mapping['instances'].items()}
    table=re.search(r'^3\.1\.1 Setup Paths Table\n(.*?)(?=\n3\.)',report,re.M|re.S)
    if not table:raise ValueError('Missing setup endpoint table')
    endpoints=[];seen=set()
    for line in table[1].splitlines():
        fields=line.split()
        if len(fields)!=9 or not fields[0].isdigit():continue
        alias,sep,pin=fields[3].rpartition('/');ff=reverse.get(alias)
        if ff not in paired or ff in seen:continue
        seen.add(ff);endpoints.append((float(fields[1]),paired[ff],ff,pin))
        if len(endpoints)>=limit:break
    drivers={b:n for n,c in cells.items() for p,bs in c['connections'].items() if c['port_directions'][p]=='output' for b in bs};edges=[]
    for n,c in cells.items():
        for pin,bs in c['connections'].items():
            if c['port_directions'][pin]!='input' or pin in ('CLK','CLKA','CLKB','CIN'):continue
            for b in bs:
                src=drivers.get(b)
                if src in points and n in points and src!=n:edges.append((src,n,1.+19.*priorities.get((n,pin),0.)**2))
    owners={(v['row'],v['column'],v['slot']):n for n,v in placed['clusters'].items()};occupied=set(owners)
    controls={(v['row'],v['column'],v['slot']//2) for v in placed['clusters'].values() if v.get('ff')}
    candidates=[]
    for slack,lut,ff,pin in endpoints:
        original=placed['clusters'][lut];slot=original['slot']
        if slot not in (0,2,4) or cells[lut]['type'] not in ('LUT1','LUT2','LUT3','LUT4'):continue
        members={lut,ff};incident=[e for e in edges if bool(e[0] in members)!=bool(e[1] in members)]
        anchors=[points[b if a in members else a] for a,b,w in incident if w>1.]
        if not anchors:continue
        centers=set(anchors)
        centers.add((round(statistics.median(p[0] for p in anchors)),round(statistics.median(p[1] for p in anchors))))
        def cost(site):
            return sum(w*(abs((site if a in members else points[a])[0]-(site if b in members else points[b])[0])+abs((site if a in members else points[a])[1]-(site if b in members else points[b])[1])) for a,b,w in incident)
        before=cost(points[lut]);options=set()
        for center in centers:
            for r in range(max(0,center[0]-2),min(device.rows,center[0]+3)):
                for c in range(max(0,center[1]-2),min(device.cols,center[1]+3)):
                    if device.grid[r][c]!=17 or (r,c) in excluded:continue
                    owner=owners.get((r,c,slot));other=placed['clusters'].get(owner,{})
                    free=owner is None and (r,c,slot//2) not in controls
                    swappable=owner!=lut and other.get('ff') in paired and not other.get('wide_ff_root') and cells[other['ff']]['connections']['CLK']==cells[ff]['connections']['CLK']
                    if free or swappable:options.add((r,c,slot))
        scored=[]
        for site in options:
            other_name=owners.get(site);partner=None
            if other_name:
                other=placed['clusters'][other_name];partner=dict(lut=other_name,ff=other['ff'],old_site=list(site))
                moved=members|{other_name,other['ff']};affected=[e for e in edges if e[0] in moved or e[1] in moved]
                changed={lut:site[:2],ff:site[:2],other_name:points[lut],other['ff']:points[lut]}
                def score(new):
                    return sum(w*(abs(new.get(a,points[a])[0]-new.get(b,points[b])[0])+abs(new.get(a,points[a])[1]-new.get(b,points[b])[1])) for a,b,w in affected)
                old_cost,new_cost=score({}),score(changed)
            else:old_cost,new_cost=before,cost(site[:2])
            scored.append((new_cost-old_cost,site,old_cost,new_cost,partner))
        for _,site,old_cost,after,partner in sorted(scored,key=lambda r:(r[0],r[1]))[:3]:
            if old_cost-after<=0:continue
            candidates.append(dict(lut=lut,ff=ff,endpoint_pin=pin,worst_slack_ns=slack,old_site=[original['row'],original['column'],slot],new_site=list(site),
                distance_before=old_cost,distance_after=after,distance_gain=old_cost-after,incident_data_control_edges=len(incident),swap_partner=partner,clock_replan_required=partner is None,
                scope='Weighted incident-net geometry only; complete routing/STA decides acceptance.'))
    return sorted(candidates,key=lambda r:(r['worst_slack_ns'],-r['distance_gain'],r['lut'],r['new_site']))


def apply(design,placed,device,change):
    name=change['lut'];row=placed['clusters'][name];site=change['new_site']
    if len(site)!=3 or any(type(v) is not int for v in site):raise ValueError('Invalid destination FF site')
    r,c,s=site
    if row.get('wide_ff_root') or not row.get('ff') or row['ff']!=change['ff'] or [row['row'],row['column'],row['slot']]!=change['old_site']:raise ValueError('Register candidate source changed')
    if s!=row['slot'] or s not in (0,2,4) or not (0<=r<device.rows and 0<=c<device.cols) or device.grid[r][c]!=17:raise ValueError('Uncalibrated destination FF slot')
    if (r,c) in exclusive_tiles(placed) or (row['row'],row['column']) in exclusive_tiles(placed):raise ValueError('Register move overlaps exclusive macro')
    partner=change.get('swap_partner');partner_name=partner['lut'] if partner else None
    if partner:
        other=placed['clusters'].get(partner_name,{})
        if other.get('ff')!=partner['ff'] or other.get('wide_ff_root') or partner['old_site']!=change['new_site'] or [other.get(k) for k in ('row','column','slot')]!=change['new_site']:raise ValueError('Register swap partner changed')
        cells=design['modules']['top']['cells']
        if cells[row['ff']]['connections']['CLK']!=cells[other['ff']]['connections']['CLK']:raise ValueError('Register swap changes clock domain')
    for n,other in placed['clusters'].items():
        if n in (name,partner_name):continue
        if (other['row'],other['column'],other['slot'])==(r,c,s):raise ValueError('Destination LUT slot is occupied')
        if other.get('ff') and (other['row'],other['column'],other['slot']//2)==(r,c,s//2):raise ValueError('Destination FF controls are occupied')
    updated=copy.deepcopy(placed);updated['clusters'][name].update(row=r,column=c,slot=s)
    if partner:updated['clusters'][partner_name].update(row=row['row'],column=row['column'],slot=row['slot'])
    validate_wide(design,updated,device);validate_carry(design,updated,device);compile_initial(design,updated)
    if updated.get('hard_clusters'):
        from ownpr_hard_architecture import HardArchitecture
        HardArchitecture().validate(design,updated,device)
    updated.pop('weighted_internal_manhattan',None);updated.update(register_move=change,physical_legality_complete=False,timing_signoff=False)
    return updated


def batch(design,placed,device,options,count):
    if not 1<=count<=8:raise ValueError('Register batch size must be 1..8')
    updated=placed;chosen=[];touched=set();skipped=[]
    for change in options:
        members={change['lut']}
        if change.get('swap_partner'):members.add(change['swap_partner']['lut'])
        if members&touched:continue
        try:candidate=apply(design,updated,device,change)
        except ValueError as error:
            skipped.append(dict(lut=change['lut'],reason=str(error)));continue
        updated=candidate;chosen.append(change);touched.update(members)
        if len(chosen)==count:break
    if not chosen:raise ValueError('No legal register move in batch')
    return updated,chosen,skipped


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('base','sta','out'):p.add_argument('--'+name,type=Path,required=True)
    p.add_argument('--rank',type=int,default=0);p.add_argument('--window',type=float,default=2.);p.add_argument('--count',type=int,default=1);a=p.parse_args()
    from workspace_budget import reserve
    from ownpr_placement_feedback import load
    reserve(ROOT,30_000_000);start=time.monotonic();design=load_json(a.base/'packed.json');placed=load_json(a.base/'placement.json');meta=load_json(a.sta/'manifest.json')
    if meta['packed_sha256']!=sha(a.base/'packed.json') or meta['placement_sha256']!=sha(a.base/'placement.json'):raise ValueError('Register move uses a different STA graph')
    weights,feedback=load(a.sta,design,relative_window_ns=a.window);device=Device();options=rank(design,placed,device,weights,read_text(a.sta/'probe.tr'),load_json(a.sta/'name_map.json'))
    if not 0<=a.rank<len(options):raise ValueError('No register candidate at requested rank')
    updated,changes,skipped=batch(design,placed,device,options[a.rank:],a.count);change=changes[0];a.out.mkdir(exist_ok=False)
    shutil.copyfile(__file__,a.out/'operator_source.py.txt')
    shutil.copyfile(a.base/'packed.json',a.out/'packed.json');(a.out/'placement.json').write_text(json.dumps(updated,indent=2));(a.out/'candidates.json').write_text(json.dumps(options,indent=2))
    m=load_json(a.base/'manifest.json');m.update(status='placed',netlist_sha256=sha(a.out/'packed.json'),placement_sha256=sha(a.out/'placement.json'),
        source_placement_sha256=sha(a.base/'placement.json'),source_sta_sha256=sha(a.sta/'probe.tr'),register_move=change,register_moves=changes,batch_skipped=skipped,batch_ranking_recomputed=False,placement_feedback=feedback,
        logical_netlist_byte_identical=True,clock_endpoints_unchanged=all(not c['clock_replan_required'] for c in changes),operator_sha256=sha(a.out/'operator_source.py.txt'),wall_seconds=time.monotonic()-start,timing_signoff=False)
    (a.out/'manifest.json').write_text(json.dumps(m,indent=2));print(json.dumps(dict(moves=changes,skipped=skipped),indent=2))


if __name__=='__main__':main()
