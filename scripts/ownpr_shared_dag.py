"""Shared multi-output LUT DAG cloning with optional old-region load isolation.

Every root retains its original output bit. Ordinary side receivers may use
an equivalent old-region root copy; hard/state/dedicated inputs never change.
All physical and timing acceptance remains external and whole-design.
"""
import copy,hashlib,json,math
from collections import defaultdict
from ownpr_path_cone import critical_segments,fast_inputs
from ownpr_critical_chain_move import locations,exclusive_tiles
from ownpr_lut_graph_feedback import LUTS
from ownpr_state_safety import protected_cells


def connectivity(design):
    cells=design['modules']['top']['cells'];drivers={};loads=defaultdict(list)
    for n,c in cells.items():
        for pin,bits in c['connections'].items():
            for i,bit in enumerate(bits):
                if c['port_directions'][pin]=='output':
                    if bit in drivers:raise ValueError('Multiple DAG drivers')
                    drivers[bit]=n
                else:loads[bit].append((n,pin,i))
    return drivers,loads


def ordered_nodes(design,members):
    drivers,_=connectivity(design);cells=design['modules']['top']['cells'];pending=set(members);order=[]
    while pending:
        ready=sorted(n for n in pending if not any(drivers.get(b) in pending for p,bs in cells[n]['connections'].items() if cells[n]['port_directions'][p]=='input' for b in bs))
        if not ready:raise ValueError('Cyclic shared LUT DAG')
        order.extend(ready);pending.difference_update(ready)
    return order


def ordinary_side_receivers(design,layout,root,members,include_macro=False,retain_point=None):
    cells=design['modules']['top']['cells'];_,loads=connectivity(design);bad=protected_cells(design);excluded=exclusive_tiles(layout)
    if layout['clusters'][root].get('ff'):return []
    bit=cells[root]['connections']['F'][0]
    wide_sites={(r['row'],r['column']) for r in layout.get('mux_clusters',{}).values()}
    return [(n,p,i) for n,p,i in loads[bit] if n not in members and n not in bad and cells[n]['type'] in LUTS and
            (layout['clusters'][n]['row'],layout['clusters'][n]['column'])!=retain_point and
            ((layout['clusters'][n]['row'],layout['clusters'][n]['column']) not in excluded or include_macro and (layout['clusters'][n]['row'],layout['clusters'][n]['column']) in wide_sites)]


def clone_shared(design,layout,members,roots,sites,tag,metrics,unload=False,fast=True,displacements=None,macro_inputs=False,retain_point=None):
    """Duplicate shared internal vertices once and preserve every root bit."""
    members=set(members);roots=set(roots);displacements=displacements or {};cells=design['modules']['top']['cells'];bad=protected_cells(design)
    if not roots or not roots<=members or set(sites)!=members or members&bad or any(cells[n]['type'] not in LUTS for n in members):raise ValueError('Invalid/protected shared DAG')
    order=ordered_nodes(design,members);drivers,_=connectivity(design)
    # Roots must be a simultaneous output frontier, never each other's inputs.
    if any(drivers.get(b) in roots for n in members for p,bs in cells[n]['connections'].items() if cells[n]['port_directions'][p]=='input' for b in bs):raise ValueError('Root is inside shared DAG frontier')
    excluded=exclusive_tiles(layout)
    if any((layout['clusters'][n]['row'],layout['clusters'][n]['column']) in excluded or tuple(sites[n])[:2] in excluded for n in members):raise ValueError('Shared DAG touches exclusive macro')
    for n in roots:
        r=layout['clusters'][n]
        if r.get('ff') and tuple(sites[n])!=(r['row'],r['column'],r['slot']):raise ValueError('Moved paired root')
    values=[b for c in cells.values() for bs in c['connections'].values() for b in bs]
    values.extend(b for row in design['modules']['top'].get('ports',{}).values() for b in row['bits']);values.extend(b for row in design['modules']['top'].get('netnames',{}).values() for b in row['bits'])
    nextbit=1+max(b for b in values if type(b) is int);names={n:'ng_shared_'+tag+'_'+n for n in members-roots}
    mapping={}
    for n in sorted(names):mapping[cells[n]['connections']['F'][0]]=nextbit;nextbit+=1
    nd=copy.deepcopy(design);nl=copy.deepcopy(layout);new=nd['modules']['top']['cells'];origins={};changed=[];legacy=[]
    for n in order:
        name=names.get(n,n)
        if name!=n and name in new:raise ValueError('Shared DAG generated-name collision')
        cell=copy.deepcopy(cells[n])
        for pin,bs in cell['connections'].items():
            if cell['port_directions'][pin]=='input':cell['connections'][pin]=[mapping.get(b,b) for b in bs]
        if n not in roots:cell['connections']['F']=[mapping[cells[n]['connections']['F'][0]]]
        new[name]=cell;origins[name]=n;changed.append(name);site=sites[n]
        if n in roots:nl['clusters'][n].update(row=site[0],column=site[1],slot=site[2])
        else:nl['clusters'][name]=dict(lut=name,ff=None,row=site[0],column=site[1],slot=site[2])
    if unload:
        for n in sorted(roots):
            row=layout['clusters'][n];oldsite=row['row'],row['column'],row['slot'];receivers=ordinary_side_receivers(design,layout,n,members,macro_inputs,retain_point)
            if not receivers or tuple(sites[n])==oldsite:continue
            name='ng_legacy_'+tag+'_'+n
            if name in new:raise ValueError('Legacy root name collision')
            cell=copy.deepcopy(cells[n]);cell['connections']['F']=[nextbit];new[name]=cell;nextbit+=1
            nl['clusters'][name]=dict(lut=name,ff=None,row=oldsite[0],column=oldsite[1],slot=oldsite[2]);origins[name]=n
            for receiver,pin,index in receivers:
                if new[receiver]['connections'][pin][index]!=cells[n]['connections']['F'][0]:raise ValueError('Side receiver already changed')
                new[receiver]['connections'][pin][index]=cell['connections']['F'][0]
            legacy.append(dict(root=n,copy=name,receivers=receivers,site=list(oldsite)))
    for n,site in displacements.items():
        row=layout['clusters'][n]
        if n in members or n in bad or row.get('ff') or cells[n]['type'] not in LUTS or (row['row'],row['column']) in excluded or tuple(site)[:2] in excluded:raise ValueError('Invalid fixed/protected displacement')
        nl['clusters'][n].update(row=site[0],column=site[1],slot=site[2])
    used=[(r['row'],r['column'],r['slot']) for r in nl['clusters'].values()]
    if len(used)!=len(set(used)):raise ValueError('Shared DAG/legacy/displaced sites overlap')
    maps={};grounds=[c['connections']['G'][0] for c in cells.values() if c['type']=='GND']
    if fast and grounds:
        # Each new DAG vertex is permuted exactly once, after all wires exist.
        for name in changed:
            original=origins[name];cell=new[name];width=int(cell['type'][-1]);source_row=layout['clusters'][original];site=sites[original]
            def estimate(i):
                # Re-estimate the changed receiver leg rather than ordering
                # solely by stale pre-move arrival times.
                pin=original+'/I'+str(i);arrival=max(metrics['setup']['arrival'].get(pin,[-math.inf,-math.inf]));bit=cells[original]['connections']['I'+str(i)][0];source=drivers.get(bit);points=locations(layout)
                if source in points:
                    olddist=abs(source_row['row']-points[source][0])+abs(source_row['column']-points[source][1]);newpoint=sites.get(source,points[source]);newdist=abs(site[0]-newpoint[0])+abs(site[1]-newpoint[1]);arrival+=.045*(newdist-olddist)
                return arrival,i
            order_inputs=sorted(range(width),key=estimate);new[name],maps[name]=fast_inputs(cell,order_inputs,grounds[0])
    change=dict(operator='shared_dag',cells=sorted(members),roots=sorted(roots),internal_copies=names,sites={n:list(sites[n]) for n in sorted(sites)},legacy=legacy,
        displaced_sites={n:list(s) for n,s in displacements.items()},destination=list(sites[sorted(roots)[0]]),neighbor_luts=sorted(roots),variant=tag,
        input_origins=origins,physical_to_original_inputs=maps,root_outputs_preserved=True,interface_cycles_changed=False)
    return nd,nl,change


def groups(design,layout,metrics,graphs,macro_inputs=False):
    sections=critical_segments(design,layout,metrics,graphs,window=1.2,limit=96);by_receiver=defaultdict(list);result=[];seen=set()
    for s in sections:
        if len(s['members'])>=2:by_receiver[s['receiver']].append(s)
        root=s['members'][-1]
        if macro_inputs or ordinary_side_receivers(design,layout,root,set(s['members'])) and not layout['clusters'][root].get('ff'):
            key=tuple(s['members']),root
            if key not in seen:result.append(dict(members=s['members'],roots=[root],receiver=s['receiver'],slack=s['slack'],unload=True));seen.add(key)
    for receiver,rows in by_receiver.items():
        for seed in rows[:12]:
            selected=[seed];members=set(seed['members']);roots={seed['members'][-1]};internal=members-roots
            for other in rows:
                root=other['members'][-1];other_internal=set(other['members'][:-1])
                if root in roots or not internal&other_internal or root in internal or roots&other_internal:continue
                if len(internal|other_internal)>8:continue
                selected.append(other);members.update(other['members']);roots.add(root);internal=members-roots
                if len(roots)>=4:break
            if len(roots)<2:continue
            key=tuple(sorted(members)),tuple(sorted(roots))
            if key in seen:continue
            seen.add(key);result.append(dict(members=sorted(members),roots=sorted(roots),receiver=receiver,slack=min(s['slack'] for s in selected),unload=False))
    return sorted(result,key=lambda r:(r['slack'],len(r['roots'])==1,-len(r['roots']),r['roots']))


def proposals(design,layout,device,metrics,graphs,limit=24,macro_inputs=False):
    points=locations(layout);excluded=exclusive_tiles(layout);occupied={(r['row'],r['column'],r['slot']):n for n,r in layout['clusters'].items()};cells=design['modules']['top']['cells'];drivers,_=connectivity(design);bad=protected_cells(design)
    geometry=[]
    for group in groups(design,layout,metrics,graphs,macro_inputs)[:24]:
        members=set(group['members']);roots=set(group['roots']);anchor=points[group['receiver']];order=ordered_nodes(design,members);boundary=[]
        for n in members:
            for pin,bs in cells[n]['connections'].items():
                if cells[n]['port_directions'][pin]!='input':continue
                for b in bs:
                    src=drivers.get(b)
                    if src not in points or src in members:continue
                    slack=min(metrics['setup']['slack'].get(n+'/'+pin,[math.inf,math.inf]));boundary.append((slack,points[src]))
        launch=min(boundary,key=lambda r:r[0])[1] if boundary else points[order[0]]
        centers=[anchor,((anchor[0]+launch[0])//2,(anchor[1]+launch[1])//2)]
        for center in dict.fromkeys(centers):
            for allow_displacement in (False,True):
                for radius in (4,8,12):
                    possible=[]
                    for r in range(max(0,center[0]-radius),min(device.rows,center[0]+radius+1)):
                        for c in range(max(0,center[1]-radius),min(device.cols,center[1]+radius+1)):
                            if device.grid[r][c]!=17 or (r,c) in excluded:continue
                            for s in range(8):
                                owner=occupied.get((r,c,s))
                                if owner is None:possible.append((r,c,s));continue
                                if allow_displacement and owner not in members and owner not in bad and not layout['clusters'][owner].get('ff') and min(metrics['setup']['slack'].get(owner+'/F',[-math.inf,-math.inf]))>.05:possible.append((r,c,s))
                    if len(possible)>=max(12,len(members)):break
                if len(possible)<len(members):continue
                sites={n:(layout['clusters'][n]['row'],layout['clusters'][n]['column'],layout['clusters'][n]['slot']) for n in roots if layout['clusters'][n].get('ff')};used=set(sites.values());tile_counts=defaultdict(int)
                for n in reversed(order):
                    if n in sites:continue
                    sinks=[sites[x][:2] for x in sites if any(cells[n]['connections']['F'][0] in bs for p,bs in cells[x]['connections'].items() if cells[x]['port_directions'][p]=='input')]
                    if n in roots:sinks.append(anchor)
                    def cost(site):
                        value=3*sum(abs(site[0]-p[0])+abs(site[1]-p[1]) for p in sinks)+3*(site[2]>=6)+2*(site in occupied)+4*tile_counts[site[:2]]
                        for pin,bs in cells[n]['connections'].items():
                            if cells[n]['port_directions'][pin]!='input':continue
                            for bit in bs:
                                src=drivers.get(bit)
                                if src not in points or src in members:continue
                                slack=min(metrics['setup']['slack'].get(n+'/'+pin,[0.,0.]));w=1+5*max(0.,1-(slack-metrics['setup']['wns'])/2)**2
                                value+=w*(abs(site[0]-points[src][0])+abs(site[1]-points[src][1]))
                        return value,site
                    site=min((s for s in possible if s not in used),key=cost);sites[n]=site;used.add(site);tile_counts[site[:2]]+=1
                evicted=sorted({occupied[s] for s in sites.values() if s in occupied and occupied[s] not in members});displacements={}
                if len(evicted)>4:continue
                for n in evicted:
                    row=layout['clusters'][n];free=[(r,c,row['slot']) for r in range(max(0,row['row']-8),min(device.rows,row['row']+9)) for c in range(max(0,row['column']-8),min(device.cols,row['column']+9)) if device.grid[r][c]==17 and (r,c) not in excluded and (r,c,row['slot']) not in occupied and (r,c,row['slot']) not in used]
                    if not free:break
                    site=min(free,key=lambda s:(abs(s[0]-row['row'])+abs(s[1]-row['column']),s));displacements[n]=site;used.add(site)
                if len(displacements)!=len(evicted):continue
                tag=hashlib.sha256(json.dumps([sorted(members),sorted(roots),sites,displacements,group['unload']],sort_keys=True).encode()).hexdigest()[:12]
                geometry.append((group,sites,displacements,tag))
    result=[];seen=set()
    # Share the bounded beam across distinct output frontiers.
    queues=defaultdict(list)
    for row in geometry:queues[tuple(row[0]['roots'])].append(row)
    while any(queues.values()) and len(result)<limit:
        for queue in queues.values():
            if not queue:continue
            g,sites,displacements,tag=queue.pop(0)
            if tag in seen:continue
            seen.add(tag)
            try:nd,nl,change=clone_shared(design,layout,g['members'],g['roots'],sites,tag,metrics,g['unload'],True,displacements,macro_inputs,points[g['receiver']] if macro_inputs else None)
            except ValueError:continue
            result.append((g['slack'],0.,'|'.join(g['roots']),nl,change,nd))
            if len(result)>=limit:break
    return result
