"""Place short critical combinational chains together, keeping all state fixed.

Weighted distances generate candidates only. Whole-tree routing and full STA
must decide acceptance; the logical netlist is byte-identical.
"""
import argparse,copy,json,re,shutil,statistics,time
from collections import defaultdict
from pathlib import Path
from ownpr_device import ROOT,Device
from ownpr_image_storage import load_json,read_text,sha
from extract_timing import ROW


def exclusive_tiles(placed):
    tiles={(r['row'],r['column']) for kind in ('arithmetic_clusters','mux_clusters') for r in placed.get(kind,{}).values()}
    for r in placed.get('hard_clusters',{}).values():tiles.update((r['row'],r['column']+i) for i in range(3))
    return tiles


def locations(placed):
    points={}
    for name,row in placed['clusters'].items():
        points[name]=(row['row'],row['column'])
        if row.get('ff'):points[row['ff']]=points[name]
    for category in ('arithmetic_clusters','mux_clusters','hard_clusters'):
        for name,row in placed.get(category,{}).items():points[name]=(row['row'],row['column'])
    for name,row in placed['fixed_io'].items():
        r,c,_=row['native_ports']['out'];points[name]=(r-1,c-1)
    return points


def chains(report,mapping,design,placed):
    reverse={v:k for k,v in mapping['instances'].items()};cells=design['modules']['top']['cells'];found={}
    excluded=exclusive_tiles(placed)
    section=re.search(r'^3\.3\.1 Setup Analysis Report\n(.*?)(?=^3\.|\Z)',report,re.M|re.S)
    if not section:raise ValueError('Missing setup path details')
    for block in re.split(r'^\s*Path\d+\s*$',section[1],flags=re.M):
        slack=re.search(r'^Slack\s*:\s*([-\d.]+)',block,re.M);data=re.search(r'Data Arrival Path:\n(.*?)(?=\nData Required Path:)',block,re.S)
        if not slack or not data:continue
        path=[];critical=set();wire=0.
        for line in data[1].splitlines():
            m=ROW.match(line)
            if not m:continue
            _,delay,kind,_,_,_,label=m.groups();alias,_,pin=label.rpartition('/');name=reverse.get(alias)
            if name is None:continue
            if kind=='tNET' and pin not in ('CLK','CLKA','CLKB','CIN'):
                critical.add((name,pin));wire+=float(delay)
            if kind in ('tINS','tC2Q'):
                row=placed['clusters'].get(name)
                eligible=(row and (row['row'],row['column']) not in excluded and not row.get('ff') and row.get('lut')==name and cells[name]['type'] in ('LUT1','LUT2','LUT3','LUT4') and pin=='F')
                path.append(name if eligible else None)
        group=[]
        for name in path+[None]:
            if name is not None:group.append(name);continue
            if len(group)>=2:
                windows=[tuple(group)] if len(group)<=4 else [tuple(group[i:i+4]) for i in range(len(group)-3)]
                for key in windows:
                    row=dict(members=list(key),worst_slack_ns=float(slack[1]),path_wire_ns=wire,critical_edges=sorted(critical))
                    if key not in found or row['worst_slack_ns']<found[key]['worst_slack_ns']:found[key]=row
            group=[]
    return sorted(found.values(),key=lambda r:(r['worst_slack_ns'],-r['path_wire_ns'],r['members']))


def rank(design,placed,device,report,mapping,max_chains=8,priorities=None):
    groups=chains(report,mapping,design,placed)[:max_chains];cells=design['modules']['top']['cells'];points=locations(placed)
    drivers={b:n for n,c in cells.items() for pin,bs in c['connections'].items() if c['port_directions'][pin]=='output' for b in bs}
    edges=[]
    for n,c in cells.items():
        for pin,bs in c['connections'].items():
            if c['port_directions'][pin]!='input' or pin in ('CLK','CLKA','CLKB','CIN'):continue
            for i,b in enumerate(bs):
                driver=drivers.get(b)
                if driver in points and n in points and driver!=n:edges.append((driver,n,pin if len(bs)==1 else f'{pin}[{i}]'))
    occupied={(r['row'],r['column'],r['slot']) for r in placed['clusters'].values()}
    excluded=exclusive_tiles(placed)
    candidates=[];seen=set()
    for group in groups:
        members=group['members'];member_set=set(members);critical=set(map(tuple,group['critical_edges']))
        def weight(b,p):return 20. if (b,p) in critical else 1.+19.*(priorities or {}).get((b,p.split('[')[0]),0.)**2
        relevant=[(a,b,weight(b,p)) for a,b,p in edges if a in member_set or b in member_set]
        anchors=[points[n] for a,b,p in edges if (a in member_set or b in member_set) and (b,p) in critical for n in (a,b) if n not in member_set]
        if not anchors:continue
        centers=[(round(statistics.median(p[0] for p in anchors)),round(statistics.median(p[1] for p in anchors))),
                 (round(statistics.mean(points[n][0] for n in members)),round(statistics.mean(points[n][1] for n in members)))]
        released={(placed['clusters'][n]['row'],placed['clusters'][n]['column'],placed['clusters'][n]['slot']) for n in members}
        owners={(r['row'],r['column'],r['slot']):n for n,r in placed['clusters'].items()}
        movable={n for n,r in placed['clusters'].items() if not r.get('ff') and r.get('lut')==n and cells[n]['type'] in ('LUT1','LUT2','LUT3','LUT4')}
        blocked={site for site,n in owners.items() if n not in movable}
        def cost(loc):return sum(w*(abs(loc[a][0]-loc[b][0])+abs(loc[a][1]-loc[b][1])) for a,b,w in relevant)
        original_cost=cost(points)
        for center in sorted(set(centers)):
            sites=[(r,c,s) for r in range(max(0,center[0]-4),min(device.rows,center[0]+5)) for c in range(max(0,center[1]-4),min(device.cols,center[1]+5))
                   if device.grid[r][c]==17 and (r,c) not in excluded for s in range(8) if (r,c,s) not in blocked]
            if len(sites)<len(members):continue
            for order in (members,list(reversed(members))):
                loc=dict(points);selected={n:(*points[n],placed['clusters'][n]['slot']) for n in members}
                for _ in range(3):
                    for n in order:
                        used={s for k,s in selected.items() if k!=n};best=None
                        for site in sites:
                            if site in used:continue
                            loc[n]=site[:2];score=(cost(loc),abs(site[0]-center[0])+abs(site[1]-center[1]),site)
                            if best is None or score<best[0]:best=(score,site)
                        if best is None:raise ValueError('Chain lost available slots')
                        selected[n]=best[1];loc[n]=best[1][:2]
                # Legalize occupied destinations by moving only other unpaired
                # LUTs into the chain's vacated slots. Charge their incident nets.
                displaced=sorted({owners[s] for s in selected.values() if s in owners and owners[s] not in member_set})
                vacancies=released-set(selected.values())
                if len(displaced)>len(vacancies):raise ValueError('Insufficient vacated sites for legalization')
                full_members=member_set|set(displaced)
                full_edges=[(a,b,weight(b,p)) for a,b,p in edges if a in full_members or b in full_members]
                def full_cost(loc):return sum(w*(abs(loc[a][0]-loc[b][0])+abs(loc[a][1]-loc[b][1])) for a,b,w in full_edges)
                legal=dict(selected)
                for n in displaced:
                    def displacement_cost(site):
                        loc[n]=site[:2];return full_cost(loc),site
                    site=min(vacancies,key=displacement_cost);loc[n]=site[:2];legal[n]=site;vacancies.remove(site)
                before=full_cost(points);after=full_cost(loc);gain=before-after;key=tuple((n,*legal[n]) for n in sorted(legal))
                if gain<=0 or key in seen:continue
                seen.add(key);candidates.append(dict(**group,sites={n:list(s) for n,s in legal.items()},displaced=displaced,distance_before=before,distance_after=after,distance_gain=gain,
                    global_criticality_used=priorities is not None,
                    ranking_scope='Chain-path edges weighted 20; other incident edges 1 + 19 * global criticality squared when enabled. Physical timing gain unproven'))
    return sorted(candidates,key=lambda r:(r['worst_slack_ns'],-r['distance_gain'],-r['path_wire_ns'],r['members']))


def apply(design,placed,device,candidate):
    result=copy.deepcopy(placed);cal=load_json(ROOT/'results/native_device_lut_004/lut_encoding_audit.json')
    if cal['source_fse_sha256']!=device.fse.sha256 or not all(x['match'] for x in cal['checks']):raise ValueError('LUT calibration mismatch')
    for n,site in candidate['sites'].items():
        r=placed['clusters'].get(n)
        if not r or r.get('ff') or r.get('lut')!=n:raise ValueError('Only unpaired ordinary LUTs may move')
        if (r['row'],r['column']) in exclusive_tiles(placed) or tuple(site[:2]) in exclusive_tiles(placed):raise ValueError('Chain move overlaps an exclusive macro')
        if len(site)!=3 or any(type(v) is not int for v in site) or not (0<=site[0]<device.rows and 0<=site[1]<device.cols and 0<=site[2]<8) or device.grid[site[0]][site[1]] not in cal['tile_types']:raise ValueError('Uncalibrated LUT site')
        result['clusters'][n].update(row=site[0],column=site[1],slot=site[2])
    from ownpr_mux_macros import validate
    from ownpr_carry_macros import validate as carry_validate
    from ownpr_ff_configuration import compile_initial
    from ownpr_hard_architecture import HardArchitecture
    sites=[(r['row'],r['column'],r['slot']) for r in result['clusters'].values()]
    if len(sites)!=len(set(sites)):raise ValueError('Overlapping moved LUTs')
    validate(design,result,device);carry_validate(design,result,device);compile_initial(design,result)
    if result.get('hard_clusters'):HardArchitecture().validate(design,result,device)
    result.pop('weighted_internal_manhattan',None);result.update(physical_legality_complete=False,timing_signoff=False,critical_chain_move=candidate)
    return result


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for n in ('base','sta','out'):p.add_argument('--'+n,type=Path,required=True)
    p.add_argument('--rank',type=int,default=0);p.add_argument('--global-criticality',action='store_true');p.add_argument('--criticality-window',type=float,default=2.);a=p.parse_args()
    if a.rank<0:p.error('Negative candidate rank')
    from workspace_budget import reserve
    reserve(ROOT,20000000);start=time.monotonic();design=load_json(a.base/'packed.json');placed=load_json(a.base/'placement.json');meta=load_json(a.sta/'manifest.json')
    if meta['packed_sha256']!=sha(a.base/'packed.json') or meta['placement_sha256']!=sha(a.base/'placement.json'):raise ValueError('STA belongs to another placement')
    from ownpr_placement_feedback import load
    priorities,feedback=load(a.sta,design,relative_window_ns=a.criticality_window if a.global_criticality else None)
    device=Device();options=rank(design,placed,device,read_text(a.sta/'probe.tr'),load_json(a.sta/'name_map.json'),priorities=priorities if a.global_criticality else None)
    if a.rank>=len(options):raise ValueError('No chain candidate at requested rank')
    change=options[a.rank];updated=apply(design,placed,device,change);a.out.mkdir(exist_ok=False)
    shutil.copyfile(a.base/'packed.json',a.out/'packed.json');(a.out/'placement.json').write_text(json.dumps(updated,indent=2));(a.out/'candidates.json').write_text(json.dumps(options,indent=2))
    m=load_json(a.base/'manifest.json');m.update(status='placed',netlist_sha256=sha(a.out/'packed.json'),placement_sha256=sha(a.out/'placement.json'),source_placement_sha256=sha(a.base/'placement.json'),
        critical_chain_move=change,placement_feedback=feedback,source_sta_sha256=sha(a.sta/'probe.tr'),logical_netlist_byte_identical=True,clock_endpoints_unchanged=True,wall_seconds=time.monotonic()-start,physical_legality_complete=False,timing_signoff=False)
    (a.out/'manifest.json').write_text(json.dumps(m,indent=2));print(json.dumps(change,indent=2))


if __name__=='__main__':main()
