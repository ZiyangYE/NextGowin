"""Make room for a wide macro by relocating nearby ordinary LUT/FF units.

Hard, carry, existing wide macros, fixed I/O and caller-protected anchors stay
fixed. Every displaced FF remains paired and needs clock retargeting/full STA.
"""
import copy
from collections import defaultdict
from ownpr_critical_chain_move import exclusive_tiles,locations


def clear_register_slot(design,layout,device,site_cost,protected=(),candidates=64,radius=3):
    """Reserve one calibrated LUT/FF pair, moving at most one ordinary LUT."""
    cells=design['modules']['top']['cells'];clusters=layout['clusters'];points=locations(layout);excluded=exclusive_tiles(layout);protected=set(protected)
    owners={(r['row'],r['column'],r['slot']):n for n,r in clusters.items()}
    controls={(r['row'],r['column'],r['slot']//2) for r in clusters.values() if r.get('ff')};options=[]
    for r,row in enumerate(device.grid):
        for c,typ in enumerate(row):
            if typ!=17 or (r,c) in excluded:continue
            cost=site_cost(r,c)
            if cost==float('inf'):continue
            for slot in (0,2,4):
                if (r,c,slot//2) in controls:continue
                n=owners.get((r,c,slot))
                if n is not None and (n in protected or clusters[n].get('ff') or clusters[n].get('wide_ff_root') or cells[n]['type'] not in ('LUT1','LUT2','LUT3','LUT4')):continue
                options.append((cost+(1 if n else 0),r,c,slot,n))
    if not options:raise ValueError('No calibrated FF control group is available')
    drivers={b:n for n,c in cells.items() for p,bs in c['connections'].items() if c['port_directions'][p]=='output' for b in bs}
    edges=[(drivers[b],n) for n,c in cells.items() for p,bs in c['connections'].items() if c['port_directions'][p]=='input' and p not in ('CLK','CLKA','CLKB','CIN') for b in bs if drivers.get(b) in points and n in points and drivers[b]!=n]
    best=None
    for _,r,c,slot,n in sorted(options,key=lambda x:x[:4])[:candidates]:
        move=None;penalty=0.
        if n:
            incident=[(a,b) for a,b in edges if n in (a,b)];old=points[n]
            def cost(point):return sum(abs((point if a==n else points[a])[0]-(point if b==n else points[b])[0])+abs((point if a==n else points[a])[1]-(point if b==n else points[b])[1]) for a,b in incident)/max(1,len(incident))
            original=cost(old);destinations=[]
            for rr in range(max(0,r-radius),min(device.rows,r+radius+1)):
                for cc in range(max(0,c-radius),min(device.cols,c+radius+1)):
                    if device.grid[rr][cc]!=17 or (rr,cc) in excluded:continue
                    for s in range(8):
                        if (rr,cc,s) in owners or (rr,cc,s)==(r,c,slot):continue
                        delta=cost((rr,cc))-original
                        score=max(0.,delta)+abs(rr-old[0])+abs(cc-old[1])+.01*abs(s-clusters[n]['slot'])
                        destinations.append((score,rr,cc,s,delta))
            if not destinations:continue
            penalty,rr,cc,s,delta=min(destinations)
            move=dict(lut=n,ff=None,old_site=[clusters[n][k] for k in ('row','column','slot')],new_site=[rr,cc,s],incident_mean_distance_change=delta)
        score=site_cost(r,c)+penalty;key=(score,bool(move),r,c,slot)
        if best is None or key<best[0]:best=(key,move)
    if best is None:raise ValueError('No local capacity for register pair legalization')
    key,move=best;_,_,r,c,slot=key;result=copy.deepcopy(layout)
    if move:
        rr,cc,s=move['new_site'];result['clusters'][move['lut']].update(row=rr,column=cc,slot=s)
    return result,(r,c,slot),dict(target_cfu=[r,c],target_slot=slot,moves=[move] if move else [],displaced_units=int(move is not None),displaced_registers=0,
        clock_replan_required=False,score=key[0],scope='One LUT/FF pair; no existing FF moves. Added FF clock still needs retargeting and full STA.')

def clear_site(design,layout,root,device,site_cost,protected=(),candidates=32,radius=5,wide_leaf_slots=None,reserved_roots=(),allow_paired_sharing=False):
    if root not in layout['clusters'] or not 1<=candidates<=64 or not 1<=radius<=8:raise ValueError('Invalid macro legalization request')
    cells=design['modules']['top']['cells'];points=locations(layout);excluded=exclusive_tiles(layout);clusters=layout['clusters']
    shared=wide_leaf_slots is not None
    ff=clusters[root].get('ff')
    if shared and (tuple(wide_leaf_slots) not in ((0,1),(0,1,2,3)) or ff and not allow_paired_sharing):raise ValueError('Uncalibrated partial wide-macro reservation')
    if allow_paired_sharing:
        from ownpr_wide_ff_lut_sharing import supports_profile,calibration
        if not shared or not ff or not supports_profile(design,ff):raise ValueError('Uncalibrated mixed FF reservation')
        calibration()
    anchors=set(protected)-{root,clusters[root].get('ff')};forbidden=excluded if shared else {points[n] for n in anchors if n in points}|excluded
    tiles=defaultdict(list)
    for n,row in clusters.items():tiles[row['row'],row['column']].append(n)
    movable=lambda n:cells[n]['type'] in ('LUT1','LUT2','LUT3','LUT4') and not clusters[n].get('wide_ff_root')
    options=[]
    for r,row in enumerate(device.grid):
        for c,typ in enumerate(row):
            if typ!=17 or (r,c) in forbidden:continue
            cost=site_cost(r,c)
            if cost==float('inf'):continue
            if shared and any(n!=root and n in reserved_roots for n in tiles[r,c]):continue
            displaced=[n for n in tiles[r,c] if n!=root and (not shared or clusters[n]['slot'] in wide_leaf_slots or clusters[n].get('ff'))]
            if shared and any(n in anchors or clusters[n].get('ff') in anchors for n in displaced):continue
            if any(not movable(n) for n in displaced):continue
            options.append((cost+2*len(displaced),r,c,displaced))
    if not options:raise ValueError('No legal macro region')
    drivers={b:n for n,c in cells.items() for pin,bs in c['connections'].items() if c['port_directions'][pin]=='output' for b in bs}
    edges=[]
    for name,c in cells.items():
        for pin,bs in c['connections'].items():
            if c['port_directions'][pin]!='input' or pin in ('CLK','CLKA','CLKB','CIN'):continue
            for bit in bs:
                source=drivers.get(bit)
                if source in points and name in points and source!=name:edges.append((source,name))
    best=None;rejected=0
    for _,r,c,displaced in sorted(options,key=lambda row:row[:3])[:candidates]:
        occupied={(p['row'],p['column'],p['slot']) for n,p in clusters.items() if n not in displaced}
        controls={(p['row'],p['column'],p['slot']//2) for n,p in clusters.items() if n not in displaced and p.get('ff')}
        moved_points=dict(points);moves=[];penalty=0.;failed=False
        for name in sorted(displaced,key=lambda n:(not bool(clusters[n].get('ff')),n)):
            row=clusters[name];ff=row.get('ff');members={name,ff} if ff else {name};origin=(row['row'],row['column'])
            incident=[(a,b) for a,b in edges if bool(a in members)!=bool(b in members)]
            def incident_cost(point):
                def pos(n):return point if n in members else moved_points[n]
                return sum(abs(pos(a)[0]-pos(b)[0])+abs(pos(a)[1]-pos(b)[1]) for a,b in incident)/max(1,len(incident))
            old_cost=incident_cost(origin);destinations=[]
            for rr in range(max(0,r-radius),min(device.rows,r+radius+1)):
                for cc in range(max(0,c-radius),min(device.cols,c+radius+1)):
                    if (rr,cc)==(r,c) or (rr,cc) in excluded or device.grid[rr][cc]!=17:continue
                    for slot in ((0,2,4) if ff else range(8)):
                        if (rr,cc,slot) in occupied or ff and (rr,cc,slot//2) in controls:continue
                        delta=incident_cost((rr,cc))-old_cost;distance=abs(rr-origin[0])+abs(cc-origin[1]);score=max(0,delta)+distance
                        destinations.append((score,rr,cc,slot,delta))
            if not destinations:failed=True;break
            score,rr,cc,slot,delta=min(destinations);occupied.add((rr,cc,slot))
            if ff:controls.add((rr,cc,slot//2))
            moved_points[name]=(rr,cc)
            if ff:moved_points[ff]=(rr,cc)
            penalty+=score;moves.append(dict(lut=name,ff=ff,old_site=[row['row'],row['column'],row['slot']],new_site=[rr,cc,slot],incident_mean_distance_change=delta))
        if failed:rejected+=1;continue
        score=site_cost(r,c)+penalty;key=(score,len(moves),r,c)
        if best is None or key<best[0]:best=(key,moves)
    if best is None:raise ValueError('Local macro legalization has no capacity')
    key,moves=best;_,_,r,c=key;result=copy.deepcopy(layout)
    for move in moves:
        rr,cc,s=move['new_site'];result['clusters'][move['lut']].update(row=rr,column=cc,slot=s)
    kept=[n for n,p in result['clusters'].items() if (p['row'],p['column'])==(r,c) and n!=root]
    if any(not shared or result['clusters'][n]['slot'] in wide_leaf_slots or result['clusters'][n].get('ff') for n in kept):raise ValueError('Macro region was not cleared')
    return result,(r,c),dict(target_cfu=[r,c],moves=moves,displaced_units=len(moves),displaced_registers=sum(bool(m['ff']) for m in moves),
        score=key[0],physical_cost=site_cost(r,c),capacity_rejections=rejected,clock_replan_required=any(m['ff'] for m in moves),
        preserved_ordinary_luts=kept,reserved_leaf_slots=list(wide_leaf_slots) if shared else list(range(8)),
        scope='Bounded geometric legalization; changes to displaced paths require complete STA')
