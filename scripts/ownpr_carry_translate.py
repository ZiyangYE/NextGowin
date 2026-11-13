"""Generate whole-carry-chain and nearby LUT placement transactions."""
import copy,math
from collections import defaultdict
from ownpr_carry_chains import extract
from ownpr_carry_macros import validate as validate_carry
from ownpr_carry_lut_sharing import calibration
from ownpr_critical_chain_move import locations
from ownpr_state_safety import protected_cells


def footprints(layout,exclude=()):
    return {(r['row'],r['column']) for n,r in layout.get('arithmetic_clusters',{}).items() if n not in exclude}


def reserved_noncarry(layout):
    result={(r['row'],r['column']) for r in layout.get('mux_clusters',{}).values()}
    for row in layout.get('hard_clusters',{}).values():result.update((row['row'],row['column']+i) for i in range(3))
    return result


def move_bundle(design,layout,device,chain,destination,metrics,graphs,couple=False,allow_zero=False):
    cells=design['modules']['top']['cells'];protected=protected_cells(design);members=set(chain['cells']);old=layout['arithmetic_clusters'][chain['cells'][0]];dr,dc=destination[0]-old['row'],destination[1]-old['column']
    if (dr,dc)==(0,0) and not couple and not allow_zero or members&protected:raise ValueError('Empty or protected carry translation')
    oldtiles=footprints(dict(arithmetic_clusters={n:layout['arithmetic_clusters'][n] for n in members}));newtiles={(r+dr,c+dc) for r,c in oldtiles};blocked=reserved_noncarry(layout)|footprints(layout,members)
    if newtiles&blocked or any(not (0<=r<device.rows and 0<=c<device.cols) or device.grid[r][c]!=17 for r,c in newtiles):raise ValueError('Carry destination intersects reserved/uncalibrated tiles')
    result=copy.deepcopy(layout);associated={n for n,r in layout['clusters'].items() if (r['row'],r['column']) in oldtiles};counts=defaultdict(int)
    for n in members:r=layout['arithmetic_clusters'][n];counts[r['row']+dr,r['column']+dc]+=1
    if associated&protected or any(layout['clusters'][n].get('ff') for n in associated):raise ValueError('Carry translation would move protected/state occupants')
    for n in members:result['arithmetic_clusters'][n]['row']+=dr;result['arithmetic_clusters'][n]['column']+=dc
    for n in associated:result['clusters'][n]['row']+=dr;result['clusters'][n]['column']+=dc
    if (dr,dc)!=(0,0) and any(counts[r['row'],r['column']]<6 for n,r in result['clusters'].items() if n in associated):raise ValueError('Translated partial carry/LUT sharing needs separate calibration')
    occupied={(r['row'],r['column'],r['slot']):n for n,r in result['clusters'].items() if n in associated};evicted=[]
    for n,row in result['clusters'].items():
        if n in associated:continue
        site=row['row'],row['column'],row['slot']
        if site[:2] in newtiles:
            if row.get('ff') or row.get('wide_ff_root') or n in protected:raise ValueError('Carry destination has a fixed FF/macro occupant')
            if row['slot']<6 or site in occupied or counts[site[:2]]<6:evicted.append(n);continue
        if site in occupied:raise ValueError('Translation overlaps an unrelated LUT')
        occupied[site]=n
    if len(evicted)>12:raise ValueError('Carry neighborhood displacement bound exceeded')
    points=locations(layout);incident=defaultdict(list)
    for e in graphs['setup']['edges']:
        if e['kind']!='wire':continue
        a=e['source'].rsplit('/',1)[0];b=e['destination'].rsplit('/',1)[0]
        if a in points and b in points and a!=b:incident[a].append(b);incident[b].append(a)
    displaced=[]
    for n in evicted:
        row=result['clusters'][n];r0,c0=row['row'],row['column'];slot=row['slot'];vacancies=[(r,c,slot) for r in range(max(0,r0-5),min(device.rows,r0+6)) for c in range(max(0,c0-5),min(device.cols,c0+6)) if device.grid[r][c]==17 and (r,c) not in blocked|newtiles and (r,c,slot) not in occupied]
        if not vacancies:raise ValueError('No legal vacancy for displaced ordinary LUT')
        def cost(site):return sum(abs(site[0]-points[x][0])+abs(site[1]-points[x][1]) for x in incident[n])+.5*(abs(site[0]-r0)+abs(site[1]-c0))
        site=min(vacancies,key=lambda s:(cost(s),s));row.update(row=site[0],column=site[1]);occupied[site]=n;displaced.append(n)
    neighbors=[]
    if couple:
        options=[]
        for e in graphs['setup']['edges']:
            if e['kind']!='wire':continue
            a=e['source'].rsplit('/',1)[0];b=e['destination'].rsplit('/',1)[0];name=target=None
            if a in members and b in result['clusters']:name,target=b,a
            elif b in members and a in result['clusters']:name,target=a,b
            if name is None or name in protected or name in associated:continue
            row=result['clusters'][name]
            if row.get('ff') or row.get('wide_ff_root') or (row['row'],row['column']) in blocked:continue
            slack=min(metrics['setup']['slack'][e['destination']]);options.append((slack,-max(e['delay']),name,target))
        for _,_,name,target in sorted(options):
            if name in neighbors:continue
            row=result['clusters'][name];t=result['arithmetic_clusters'][target];vacancies=[(t['row'],t['column'],s) for s in (6,7) if (t['row'],t['column'],s) not in occupied]
            if counts[t['row'],t['column']]<6:continue
            if not vacancies:continue
            oldsite=row['row'],row['column'],row['slot'];site=vacancies[0];occupied.pop(oldsite);row.update(row=site[0],column=site[1],slot=site[2]);occupied[site]=name;neighbors.append(name)
            if len(neighbors)>=2:break
    result['mixed_carry_lut_calibration']=calibration()['capture_sha256'];validate_carry(design,result,device)
    return result,dict(head=chain['cells'][0],cells=chain['cells'],destination=list(destination),offset=[dr,dc],associated_luts=sorted(associated),displaced_luts=displaced,neighbor_luts=neighbors,logic_byte_identical=True,interface_cycles_changed=False)


def copy_sum_neighbors(design,layout,chain,metrics,graphs):
    """Copy SUM-consuming logic for nearby LUT sinks; retain old fanouts."""
    members=set(chain['cells']);cells=design['modules']['top']['cells'];protected=protected_cells(design);points=locations(layout);occupied={(r['row'],r['column'],r['slot']) for r in layout['clusters'].values()};counts=defaultdict(int)
    for n in members:r=layout['arithmetic_clusters'][n];counts[r['row'],r['column']]+=1
    options=[]
    for edge in graphs['setup']['edges']:
        if edge['kind']!='wire' or not edge['source'].endswith('/SUM'):continue
        alu=edge['source'].rsplit('/',1)[0];root=edge['destination'].rsplit('/',1)[0]
        if alu not in members or root not in layout['clusters'] or root in protected:continue
        target=layout['arithmetic_clusters'][alu];r,c=target['row'],target['column']
        if counts[r,c]!=6:continue
        bit=cells[root]['connections']['F'][0]
        for sink,e in [(e['destination'],e) for e in graphs['setup']['edges'] if e['kind']=='wire' and e['source']==root+'/F']:
            name,pin=sink.rsplit('/',1)
            if name not in layout['clusters'] or name in protected or not pin.startswith('I'):continue
            gain=abs(points[root][0]-points[name][0])+abs(points[root][1]-points[name][1])-abs(r-points[name][0])-abs(c-points[name][1])
            if gain<=0:continue
            for slot in (6,7):
                if (r,c,slot) not in occupied:options.append((min(metrics['setup']['slack'][sink]),-gain,root,name,pin,(r,c,slot)))
    new=copy.deepcopy(design);placed=copy.deepcopy(layout);cells=new['modules']['top']['cells'];values=[b for cell in cells.values() for bs in cell['connections'].values() for b in bs];values.extend(b for row in new['modules']['top'].get('netnames',{}).values() for b in row['bits']);values.extend(b for row in new['modules']['top'].get('ports',{}).values() for b in row['bits']);nextbit=1+max(b for b in values if isinstance(b,int));chosen=[];used=set()
    for _,_,root,sink,pin,site in sorted(options):
        if site in occupied or root in used:continue
        name='ng_macro_copy_'+str(len(chosen))+'_'+root
        if name in cells:continue
        cell=copy.deepcopy(cells[root]);cell['connections']['F']=[nextbit];cells[name]=cell;cells[sink]['connections'][pin]=[nextbit];nextbit+=1
        placed['clusters'][name]=dict(lut=name,ff=None,row=site[0],column=site[1],slot=site[2]);occupied.add(site);used.add(root);chosen.append(dict(source=root,copy=name,receiver=sink,pin=pin,site=list(site)))
        if len(chosen)>=2:break
    if not chosen:raise ValueError('No useful carry SUM-neighbor copy')
    return new,placed,chosen


def proposals(design,layout,device,metrics,graphs,limit=16):
    cells=design['modules']['top']['cells'];points=locations(layout);slacks=metrics['setup']['slack'];records=[]
    for chain in extract(design['modules']['top'])['chains']:
        members=set(chain['cells']);head=layout['arithmetic_clusters'][chain['cells'][0]];edges=[];anchors=[]
        for e in graphs['setup']['edges']:
            if e['kind']!='wire':continue
            a=e['source'].rsplit('/',1)[0];b=e['destination'].rsplit('/',1)[0]
            if (a in members)==(b in members) or a not in points or b not in points:continue
            member=a if a in members else b;other=b if a in members else a;offset=layout['arithmetic_clusters'][member]['column']-head['column'];s=min(slacks[e['destination']]);weight=40. if s<metrics['setup']['wns']+.5 else 10. if s<metrics['setup']['wns']+1. else 1.
            edges.append((a,b,weight));anchors.append((s,points[other][0],points[other][1]-offset,weight))
        if not anchors:continue
        worst=min(x[0] for x in anchors)
        if worst>metrics['setup']['wns']+1.:continue
        def median(index):
            rows=sorted((a[index],a[3]) for a in anchors);total=sum(v for _,v in rows);acc=0
            for value,weight in rows:
                acc+=weight
                if acc>=total/2:return value
        center=median(1),median(2);centers=[center,((center[0]+head['row'])//2,(center[1]+head['column'])//2)]+[(r,c) for _,r,c,_ in sorted(anchors)[:4]];candidates=set()
        for r,c in centers:
            for dr,dc in ((0,0),(-1,0),(1,0),(0,-1),(0,1)):candidates.add((r+dr,c+dc))
        def cost(pos):
            dr,dc=pos[0]-head['row'],pos[1]-head['column']
            def point(n):return (points[n][0]+dr,points[n][1]+dc) if n in members else points[n]
            return sum(w*(abs(point(a)[0]-point(b)[0])+abs(point(a)[1]-point(b)[1])) for a,b,w in edges)
        before=cost((head['row'],head['column']))
        # First find feasible carry corridors across the chip. Restricting
        # geometry candidates before checking FF occupancy misses all legal
        # moves for long chains in the densely packed center.
        forbidden=reserved_noncarry(layout)|footprints(layout,members)|{(r['row'],r['column']) for r in layout['clusters'].values() if r.get('ff') or r.get('wide_ff_root')}
        width=(chain['length']+5)//6
        for r in range(device.rows):
            for c in range(device.cols-width+1):
                if all((r,c+i) not in forbidden and device.grid[r][c+i]==17 for i in range(width)):candidates.add((r,c))
        candidates={p for p in candidates if 0<=p[0]<device.rows and 0<=p[1]<=device.cols-width and all((p[0],p[1]+i) not in forbidden and device.grid[p[0]][p[1]+i]==17 for i in range(width))}
        ranked=sorted(candidates,key=lambda p:(cost(p),p))[:12]
        if (head['row'],head['column']) not in ranked:ranked.append((head['row'],head['column']))
        for pos in ranked:
            for variant in ('copy','move','translate'):
                try:
                    new,change=move_bundle(design,layout,device,chain,pos,metrics,graphs,variant=='move',variant=='copy')
                    candidate_design=design
                    if variant=='copy':candidate_design,new,copies=copy_sum_neighbors(design,new,chain,metrics,graphs);change.update(copied_luts=copies,logic_byte_identical=False)
                except ValueError:continue
                if new==layout and candidate_design==design:continue
                change['variant']=variant;records.append((worst,cost(pos)-before,chain['cells'][0],new,change,candidate_design))
    queues={};ordered=[]
    for row in sorted(records,key=lambda r:(r[0],r[1],r[2])):queues.setdefault(row[2],[]).append(row)
    while any(queues.values()) and len(ordered)<limit:
        for name in queues:
            if queues[name] and len(ordered)<limit:ordered.append(queues[name].pop(0))
    return ordered
