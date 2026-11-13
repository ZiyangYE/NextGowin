"""Clone critical combinational path cones and place them near their receivers.

The root keeps its original output and all original hard/state connections.
Shared upstream functions remain for the other receivers. Geometry ranks
transactions only; complete measured trees and full setup/hold decide them.
"""
import copy,hashlib,json,math
from collections import defaultdict
from ownpr_bidir_timing import topology
from ownpr_critical_chain_move import locations,exclusive_tiles
from ownpr_lut_graph_feedback import LUTS
from ownpr_state_safety import protected_cells


def critical_segments(design,layout,metrics,graphs,window=1.,limit=64):
    cells=design['modules']['top']['cells'];protected=protected_cells(design)
    excluded=exclusive_tiles(layout);points=locations(layout)
    allowed={n for n,c in cells.items() if c['type'] in LUTS and n not in protected
             and (layout['clusters'][n]['row'],layout['clusters'][n]['column']) not in excluded}
    graph=graphs['setup'];_,incoming,_=topology(graph);at=metrics['setup']['arrival'];segments={}
    endpoints=sorted(metrics['setup']['endpoints'].items(),key=lambda r:(r[1],r[0]))
    for endpoint,slack in endpoints[:limit]:
        if slack>metrics['setup']['wns']+window:break
        for polarity in (0,1):
            node=endpoint;pol=polarity;trace=[]
            while incoming[node]:
                options=[(at[graph['edges'][i]['source']][a]+graph['edges'][i]['delay'][b],i,a)
                         for i in incoming[node] for a,b in graph['edges'][i]['transitions'] if b==pol]
                if not options:break
                _,index,sp=max(options);e=graph['edges'][index];trace.append(e);node=e['source'];pol=sp
            trace.reverse();current=[]
            # Select contiguous ordinary-LUT sections between fixed primitives.
            for e in trace+[dict(source=endpoint,destination=endpoint,kind='end')]:
                src=e['source'].rsplit('/',1)[0];dst=e['destination'].rsplit('/',1)[0]
                if e['kind']=='cell' and src==dst and src in allowed:
                    if src not in current:current.append(src)
                elif e['kind']=='wire' and dst not in allowed or e['kind']=='end':
                    if current:
                        for width in sorted({1,min(3,len(current)),min(5,len(current))}):
                            members=tuple(current[-width:]);receiver=dst if dst in points else members[-1]
                            key=members,receiver
                            if key not in segments or slack<segments[key]['slack']:
                                segments[key]=dict(members=list(members),receiver=receiver,slack=slack)
                    current=[]
                elif e['kind']=='cell' and (src not in allowed or dst not in allowed):current=[]
    return sorted(segments.values(),key=lambda x:(x['slack'],x['members'],x['receiver']))


def clone_cone(design,layout,members,sites,tag):
    """Copy each upstream LUT once; root output and original loads stay intact."""
    if not members or len(members)!=len(set(members)) or len(sites)!=len(members):raise ValueError('Invalid path cone')
    cells=design['modules']['top']['cells'];protected=protected_cells(design);root=members[-1]
    if set(members)&protected or any(cells[n]['type'] not in LUTS for n in members):raise ValueError('Protected/nonordinary cone')
    old=layout['clusters'][root];root_site=old['row'],old['column'],old['slot']
    if old.get('ff') and tuple(sites[-1])!=root_site:raise ValueError('Cannot move a paired root')
    if len(set(map(tuple,sites)))!=len(sites):raise ValueError('Cone slots overlap')
    occupied={(r['row'],r['column'],r['slot']):n for n,r in layout['clusters'].items()}
    if any(tuple(s) in occupied and not (n==root and occupied[tuple(s)]==root) for n,s in zip(members,sites)):raise ValueError('Cone destination occupied')
    values=[b for c in cells.values() for bs in c['connections'].values() for b in bs]
    values.extend(b for r in design['modules']['top'].get('netnames',{}).values() for b in r['bits'])
    values.extend(b for r in design['modules']['top']['ports'].values() for b in r['bits'])
    nextbit=1+max(b for b in values if type(b) is int);mapping={};names={}
    for n in members[:-1]:
        names[n]='ng_path_copy_'+tag+'_'+n
        if names[n] in cells:raise ValueError('Cone generated-name collision')
        mapping[cells[n]['connections']['F'][0]]=nextbit;nextbit+=1
    result=copy.deepcopy(design);placed=copy.deepcopy(layout);new=result['modules']['top']['cells'];copies=[]
    for n,site in zip(members,sites):
        name=names.get(n,n);cell=copy.deepcopy(cells[n])
        for pin,bits in cell['connections'].items():
            if cell['port_directions'][pin]=='input':cell['connections'][pin]=[mapping.get(b,b) for b in bits]
        if n!=root:
            cell['connections']['F']=[mapping[cells[n]['connections']['F'][0]]]
            placed['clusters'][name]=dict(lut=name,ff=None,row=site[0],column=site[1],slot=site[2]);copies.append(name)
        else:placed['clusters'][name].update(row=site[0],column=site[1],slot=site[2])
        new[name]=cell
    return result,placed,dict(operator='path_cone',cells=members,root=root,copies=copies,sites=list(map(list,sites)),
        destination=list(sites[-1]),neighbor_luts=members[:-1],variant=tag,root_output_preserved=True,
        fixed_state_and_hard_cells=True,interface_cycles_changed=False)


def fast_inputs(cell,order,ground):
    """Put late inputs on the fast calibrated LUT4 pins with exact INIT change."""
    width=int(cell['type'][-1])
    if sorted(order)!=list(range(width)):raise ValueError('Invalid LUT input permutation')
    result=copy.deepcopy(cell);padding=4-width;word=int(cell['parameters']['INIT'],2);truth=0
    for assignment in range(16):
        old=sum(((assignment>>(padding+j))&1)<<i for j,i in enumerate(order))
        truth|=((word>>old)&1)<<assignment
    bits=[ground]*padding+[cell['connections']['I'+str(i)][0] for i in order]
    result.update(type='LUT4',parameters={'INIT':format(truth,'016b')},
        connections={**{'I'+str(i):[b] for i,b in enumerate(bits)},'F':list(cell['connections']['F'])},
        port_directions={**{'I'+str(i):'input' for i in range(4)},'F':'output'})
    return result,{'I'+str(padding+j):'I'+str(i) for j,i in enumerate(order)}


def optimize_inputs(design,members,change,metrics):
    cells=design['modules']['top']['cells'];grounds=[c['connections']['G'][0] for c in cells.values() if c['type']=='GND']
    if not grounds:return design,change
    origins={**{n:n for n in members},**{n:old for n,old in zip(change['copies'],members[:-1])}};maps={}
    for name in [*change['copies'],members[-1]]:
        original=origins[name];cell=cells[name];width=int(cell['type'][-1])
        order=sorted(range(width),key=lambda i:(max(metrics['setup']['arrival'].get(original+'/I'+str(i),[-math.inf,-math.inf])),i))
        cells[name],maps[name]=fast_inputs(cell,order,grounds[0])
    change=dict(change,input_origins=origins,physical_to_original_inputs=maps,fast_pin_permutation=True)
    return design,change


def prioritize(nets,logical,metrics,change,window=2.):
    """Build routing-only sink weights; physical binding metadata stays intact."""
    origins=change.get('input_origins',{});maps=change.get('physical_to_original_inputs',{});weights=defaultdict(dict)
    for row in logical:
        name,pin=row['instance'],row['pin'];old=origins.get(name,name);oldpin=maps.get(name,{}).get(pin,pin)
        slack=min(metrics['setup']['slack'].get(old+'/'+oldpin,[metrics['setup']['wns'],metrics['setup']['wns']]))
        value=.05+.95*max(0.,min(1.,1-(slack-metrics['setup']['wns'])/window))**2
        target=weights[row['net']];target[row['node']]=max(value,target.get(row['node'],0.))
    return [dict(n,criticality=max((weights[n['name']].get(s,.05) for s in n['sinks']),default=.05),
                 sink_criticalities={s:weights[n['name']].get(s,.05) for s in n['sinks']}) for n in nets]


def measured_batch(design,layout,source,metrics,maximum=8):
    """Compose measured nonworsening path cones, then require a fresh joint route."""
    from ownpr_image_storage import load_json,sha
    rows=[r for r in load_json(source/'trials.json') if r.get('admissible') and r.get('change',{}).get('operator')=='path_cone']
    rows.sort(key=lambda r:(-r['setup']['wns'],-r['setup']['tns']))
    nd,nl=design,layout;used=set();chosen=[];origins={};maps={};boundary_counts=defaultdict(int)
    drivers={b:n for n,c in design['modules']['top']['cells'].items() for p,bs in c['connections'].items() if c['port_directions'][p]=='output' for b in bs}
    for row in rows:
        c=row['change'];members=c['cells']
        if used&set(members):continue
        boundary={drivers.get(b) for n in members for p,bs in nd['modules']['top']['cells'][n]['connections'].items() if nd['modules']['top']['cells'][n]['port_directions'][p]=='input' for b in bs}-{None}-set(members)
        if any(boundary_counts[n]>=2 for n in boundary):continue
        try:
            dd,ll,cc=clone_cone(nd,nl,members,c['sites'],c['variant']+'_batchfast')
            dd,cc=optimize_inputs(dd,members,cc,metrics)
        except ValueError:continue
        nd,nl=dd,ll;chosen.append(cc);used.update(members);origins.update(cc.get('input_origins',{}));maps.update(cc.get('physical_to_original_inputs',{}))
        for n in boundary:boundary_counts[n]+=1
        if len(chosen)>=maximum:break
    if not chosen:raise ValueError('No compatible measured path cone batch')
    change=dict(operator='path_cone_batch',cells=sorted(used),destination=[0,0],neighbor_luts=[c['root'] for c in chosen],variant='batch_'+sha(source/'trials.json')[:12],
                individuals=chosen,input_origins=origins,physical_to_original_inputs=maps,interface_cycles_changed=False)
    return nd,nl,change


def proposals(design,layout,device,metrics,graphs,limit=32,window=1.):
    points=locations(layout);excluded=exclusive_tiles(layout);occupied={(r['row'],r['column'],r['slot']) for r in layout['clusters'].values()}
    cells=design['modules']['top']['cells'];drivers={b:n for n,c in cells.items() for p,bs in c['connections'].items() if c['port_directions'][p]=='output' for b in bs}
    segments=critical_segments(design,layout,metrics,graphs,window);queues={}
    for section in segments:
        members=section['members'];root=members[-1];member_set=set(members);receiver=section['receiver'];old=layout['clusters'][root];oldsite=old['row'],old['column'],old['slot'];anchor=points[receiver]
        # Root/receiver locality and a balanced boundary alternative compete.
        boundaries=[points[drivers[b]] for n in members for pin,bs in cells[n]['connections'].items() if cells[n]['port_directions'][pin]=='input' for b in bs if drivers.get(b) in points and drivers[b] not in member_set]
        centers=[anchor,((anchor[0]+points[members[0]][0])//2,(anchor[1]+points[members[0]][1])//2)]
        if boundaries:centers.append((round((2*anchor[0]+sum(p[0] for p in boundaries)/len(boundaries))/3),round((2*anchor[1]+sum(p[1] for p in boundaries)/len(boundaries))/3)))
        for center in dict.fromkeys(centers):
            # A globally small design may still have a completely packed
            # critical region. Expand the legal vacancy window before ranking.
            for radius in (3,5,8,12):
                free=[(r,c,s) for r in range(max(0,center[0]-radius),min(device.rows,center[0]+radius+1)) for c in range(max(0,center[1]-radius),min(device.cols,center[1]+radius+1)) if device.grid[r][c]==17 and (r,c) not in excluded for s in range(8) if (r,c,s) not in occupied]
                if len(free)>=max(16,len(members)):break
            if len(free)<len(members):continue
            for spread in (0,2):
                sites={root:oldsite} if old.get('ff') else {};used=set(sites.values())
                # Place backwards from the receiver. Late-input wire weights
                # reduce avoidable upstream detours; final timing is measured.
                for n in reversed(members):
                    if n in sites:continue
                    sinks=[sites[x][:2] for x in sites if any(cells[n]['connections']['F'][0] in bs for p,bs in cells[x]['connections'].items() if cells[x]['port_directions'][p]=='input')]
                    if n==root:sinks=[anchor]
                    def cost(site):
                        wire=sum(abs(site[0]-p[0])+abs(site[1]-p[1]) for p in sinks)*3
                        for pin,bs in cells[n]['connections'].items():
                            if cells[n]['port_directions'][pin]!='input':continue
                            for bit in bs:
                                driver=drivers.get(bit)
                                if driver not in points or driver in member_set:continue
                                slack=min(metrics['setup']['slack'].get(n+'/'+pin,[0.,0.]));weight=1+8*max(0.,1-(slack-metrics['setup']['wns'])/2)**2
                                wire+=weight*(abs(site[0]-points[driver][0])+abs(site[1]-points[driver][1]))
                        return wire+spread*(abs(site[0]-center[0])+abs(site[1]-center[1])),site[2]>=6,site
                    site=min((s for s in free if s not in used),key=cost);sites[n]=site;used.add(site)
                ordered=[sites[n] for n in members];tag=hashlib.sha256(json.dumps([members,ordered]).encode()).hexdigest()[:10]
                key=root,tuple(members)
                queues.setdefault(key,[]).append((section['slack'],members,ordered,tag))
    # Round-robin physical cones so one path does not fill the search beam.
    result=[];seen=set()
    while any(queues.values()) and len(result)<limit:
        for key in queues:
            if not queues[key]:continue
            row=queues[key].pop(0);slack,members,ordered,tag=row;tag+='_fast'
            if tag in seen:continue
            seen.add(tag);nd,nl,change=clone_cone(design,layout,members,ordered,tag)
            nd,change=optimize_inputs(nd,members,change,metrics)
            result.append((slack,0.,members[-1],nl,change,nd))
            if len(result)>=limit:break
    return result


def carry_copies(design,layout,device,metrics,graphs,limit=1):
    """Competing small carry-neighbor transactions share the same objective."""
    from ownpr_carry_chains import extract
    from ownpr_carry_translate import copy_sum_neighbors
    from ownpr_carry_lut_sharing import calibration
    from ownpr_carry_macros import validate
    chains=extract(design['modules']['top'])['chains'];ranked=[]
    for chain in chains:
        slack=min((min(metrics['setup']['slack'].get(n+'/SUM',[math.inf,math.inf])) for n in chain['cells']),default=math.inf)
        if slack<=metrics['setup']['wns']+.5:ranked.append((slack,chain))
    result=[]
    for slack,chain in sorted(ranked,key=lambda r:(r[0],r[1]['cells'][0])):
        try:
            nd,nl,copies=copy_sum_neighbors(design,layout,chain,metrics,graphs)
            nl['mixed_carry_lut_calibration']=calibration()['capture_sha256'];validate(nd,nl,device)
        except ValueError:continue
        head=chain['cells'][0];row=layout['arithmetic_clusters'][head]
        change=dict(operator='carry_neighbor_copy',head=head,cells=chain['cells'],copies=copies,destination=[row['row'],row['column']],offset=[0,0],neighbor_luts=[r['source'] for r in copies],variant='carry_copy_'+head,interface_cycles_changed=False)
        result.append((slack,0.,head,nl,change,nd))
        if len(result)>=limit:break
    return result
