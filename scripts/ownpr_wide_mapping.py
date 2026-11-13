"""Map certified 5..7-input cuts to isolated native wide-LUT CFUs."""
import copy,hashlib,json,math
from ownpr_multilevel_cut import Cuts
from ownpr_mux_macros import tree,validate as validate_mux
from ownpr_critical_chain_move import locations,exclusive_tiles
from ownpr_lut_graph_feedback import LUTS
from ownpr_state_safety import protected_cells


def implement(design,layout,cut,site,ordered_boundary,tag,buffered=False):
    verified=Cuts(design,k=7,depth=8).function(cut['root'],cut['evaluation_boundary'])
    if verified!=cut or not 5<=cut['cut_width']<=7 or sorted(ordered_boundary)!=sorted(cut['cut_bits']):raise ValueError('Invalid wide-cut certificate/order')
    root=cut['root'];old=design['modules']['top']['cells'];prior=layout['clusters'][root]
    if root in protected_cells(design) or old[root]['type'] not in LUTS or prior.get('ff') and not buffered:raise ValueError('Protected or paired direct wide root')
    r,c=site
    if (r,c) in exclusive_tiles(layout) or any((x['row'],x['column'])==(r,c) for x in layout['clusters'].values()):raise ValueError('Wide CFU is not isolated/vacant')
    new=copy.deepcopy(design);placed=copy.deepcopy(layout);cells=new['modules']['top']['cells'];module=new['modules']['top']
    values=[b for cell in cells.values() for bs in cell['connections'].values() for b in bs]+[b for row in module.get('ports',{}).values() for b in row['bits']]+[b for row in module.get('netnames',{}).values() for b in row['bits']]
    nextbit=1+max(b for b in values if type(b) is int)
    def allocate():
        nonlocal nextbit
        value=nextbit;nextbit+=1;return value
    ground=next((cell['connections']['G'][0] for cell in cells.values() if cell['type']=='GND'),None)
    if buffered and ground is None:raise ValueError('Buffer requires existing physical GND')
    output=old[root]['connections']['F'][0];wide_root='ng_wide_'+tag+'_'+root if buffered else root;wide_output=allocate() if buffered else output
    if buffered and wide_root in cells:raise ValueError('Generated wide root already exists')
    boundary=list(ordered_boundary);width=len(boundary);word=0;truth=int(cut['truth_hex'],16)
    for assignment in range(1<<width):
        values=dict(zip(boundary,[(assignment>>i)&1 for i in range(width)]));address=sum(values[b]<<i for i,b in enumerate(cut['cut_bits']));word|=((truth>>address)&1)<<assignment
    added=[]
    def add(name,kind,connections,init=None):
        if name in cells and name!=root:raise ValueError('Generated wide cell collision')
        metadata={k:copy.deepcopy(v) for k,v in old[root].items() if k not in ('type','parameters','attributes','port_directions','connections')} if name==root else {}
        cells[name]=dict(metadata,type=kind,parameters={} if init is None else {'INIT':init},attributes=copy.deepcopy(old[root].get('attributes',{})) if name==root else {},connections={p:[b] for p,b in connections.items()},port_directions={p:'output' if p in ('F','O') else 'input' for p in connections})
        if name!=root:added.append(name)
    current=[]
    for i in range(1<<(width-4)):
        name='ng_wide_'+tag+'_'+root+'_leaf_'+str(i);bit=allocate();leafword=(word>>(16*i))&65535;bits=boundary[:4]
        if ground is not None:
            active=[j for j in range(4) if any(((leafword>>a)&1)!=((leafword>>(a^(1<<j)))&1) for a in range(16))]
            # Keep one timed input on a constant cofactor: constant-output
            # seed semantics are not yet calibrated for a completely tied LUT.
            if not active:active=[3]
            padding=4-len(active);rewritten=0
            for a in range(16):
                address=sum(((a>>(padding+p))&1)<<j for p,j in enumerate(active));rewritten|=((leafword>>address)&1)<<a
            leafword=rewritten;bits=[ground]*padding+[boundary[j] for j in active]
        add(name,'LUT4',{**{'I'+str(j):bits[j] for j in range(4)},'F':bit},format(leafword,'016b'));current.append(bit)
    for level in range(5,width+1):
        following=[]
        for i in range(len(current)//2):
            final=level==width;name=wide_root if final else 'ng_wide_'+tag+'_'+root+'_mux_'+str(level)+'_'+str(i);bit=wide_output if final else allocate()
            add(name,'MUX2_LUT'+str(level),dict(I0=current[2*i],I1=current[2*i+1],S0=boundary[level-1],O=bit));following.append(bit)
        current=following
    if buffered:
        cell=copy.deepcopy(old[root]);cell.update(type='LUT4',parameters={'INIT':format(0xFF00,'016b')},connections=dict(I0=[ground],I1=[ground],I2=[ground],I3=[wide_output],F=[output]),port_directions=dict(I0='input',I1='input',I2='input',I3='input',F='output'));cells[root]=cell
    else:placed['clusters'].pop(root)
    root_slot={5:0,6:1,7:3}[width];spec=tree(new,wide_root,root_slot)
    for name,slot in spec['leaves'].items():placed['clusters'][name]=dict(lut=name,ff=None,row=r,column=c,slot=slot)
    for name,record in spec['muxes'].items():placed.setdefault('mux_clusters',{})[name]=dict(record,row=r,column=c)
    placed.setdefault('wide_macros',[]).append(dict(root=wide_root,row=r,column=c,root_slot=root_slot))
    return new,placed,dict(operator='wide_mapping',root=root,wide_root=wide_root,cut=cut,cells=sorted(set(cut['interior'])|set(added)|{root}),added_cells=added,buffered=buffered,
        destination=list(site),neighbor_luts=sorted(spec['leaves']),variant=tag,ordered_boundary=boundary,cofactor_input_dependence_pruned=ground is not None,interface_cycles_changed=False)


def proposals(design,layout,device,metrics,graphs,limit=24,global_frontier=False,frontier_audit=None):
    cells=design['modules']['top']['cells'];protected=protected_cells(design);excluded=exclusive_tiles(layout);points=locations(layout);cuts=Cuts(design,k=7,depth=8,limit=128)
    drivers={b:n+'/'+p for n,c in cells.items() for p,bs in c['connections'].items() if c['port_directions'][p]=='output' for b in bs}
    private={b for c in cells.values() if c['type'].startswith('MUX2_LUT') for pin in ('I0','I1') for b in c['connections'][pin]}
    occupied=excluded|{(r['row'],r['column']) for r in layout['clusters'].values()};free=[(r,c) for r in range(device.rows) for c in range(device.cols) if device.grid[r][c]==17 and (r,c) not in occupied]
    slacks=metrics['setup']['slack'];arrivals=metrics['setup']['arrival']
    eligible=[n for n,c in cells.items() if c['type'] in LUTS and n not in protected and n+'/F' in slacks and (layout['clusters'][n]['row'],layout['clusters'][n]['column']) not in excluded]
    if global_frontier:
        from ownpr_global_timing_frontier import select_roots
        roots,audit=select_roots(design,layout,graphs['setup'],metrics['setup'],eligible,limit=40)
        if frontier_audit is not None:frontier_audit.update(audit)
    else:roots=sorted((n for n in eligible if min(slacks[n+'/F'])<=metrics['setup']['wns']+.65),key=lambda n:(min(slacks[n+'/F']),n))[:40]
    queues={}
    for root in roots:
        row=layout['clusters'][root];sinks=[points[e['destination'].rsplit('/',1)[0]] for e in graphs['setup']['edges'] if e['kind']=='wire' and e['source']==root+'/F' and e['destination'].rsplit('/',1)[0] in points];found=[]
        for boundary in cuts.enumerate(cells[root]['connections']['F'][0]):
            if boundary&private:continue
            try:cut=cuts.function(root,boundary)
            except ValueError:continue
            if not 5<=cut['cut_width']<=7 or cut['interior_count']<2 or any(b not in drivers or drivers[b] not in arrivals for b in cut['cut_bits']):continue
            optimistic=max(arrivals[root+'/F'])-max(max(arrivals[drivers[b]]) for b in cut['cut_bits'])-.8
            if optimistic<.1:continue
            found.append((-optimistic,-cut['interior_count'],cut['cut_width'],cut))
        chosen=[]
        for width in (7,6,5):
            candidates=[r for r in found if r[3]['cut_width']==width]
            if candidates:chosen.append(min(candidates,key=lambda r:r[:3])[3])
        for cut in chosen:
            order=sorted(cut['cut_bits'],key=lambda b:(max(arrivals[drivers[b]]),b));boundary_points={b:points.get(drivers[b].rsplit('/',1)[0]) for b in order}
            for buffered in ((True,) if row.get('ff') else (False,True)):
                def cost(site):
                    value=3*sum(abs(site[0]-p[0])+abs(site[1]-p[1]) for p in ([points[root]] if buffered else sinks))
                    for b,p in boundary_points.items():
                        if p is None:continue
                        late=max(arrivals[drivers[b]]);weight=1+max(0.,late-max(arrivals[root+'/F'])+3.)
                        value+=weight*(abs(site[0]-p[0])+abs(site[1]-p[1]))
                    return value,abs(site[0]-points[root][0])+abs(site[1]-points[root][1]),site
                sites=sorted(free,key=cost)[:3]
                for site in sites:
                    tag=hashlib.sha256(json.dumps([root,cut['cut_bits'],order,site,buffered]).encode()).hexdigest()[:12]
                    queues.setdefault(root,[]).append((cut,site,order,tag,buffered))
    result=[]
    while any(queues.values()) and len(result)<limit:
        for root,queue in queues.items():
            if not queue:continue
            cut,site,order,tag,buffered=queue.pop(0)
            try:nd,nl,change=implement(design,layout,cut,site,order,tag,buffered);validate_mux(nd,nl,device)
            except ValueError:continue
            result.append((min(slacks[root+'/F']),0.,root,nl,change,nd))
            if len(result)>=limit:break
    if global_frontier and frontier_audit is not None:
        frontier_audit['offered_roots']=list(dict.fromkeys(row[2] for row in result))
        frontier_audit['offered_candidates']=len(result)
        frontier_audit['physical_candidates_measured']=False
    return result


def measured_batch(design,layout,device,source,maximum=6):
    """Compose disjoint measured wide cuts and legalize distinct vacant CFUs."""
    from ownpr_image_storage import load_json,sha
    rows=[r for r in load_json(source/'trials.json') if r.get('admissible') and r.get('change',{}).get('operator')=='wide_mapping']
    rows.sort(key=lambda r:(-r['setup']['wns'],-r['setup']['tns']))
    nd,nl=design,layout;roots=set();interiors=set();selected=[]
    for row in rows:
        change=row['change'];root=change['root'];cut=change['cut']
        if root in roots or root in interiors or roots&set(cut['interior']) or nd['modules']['top']['cells'][root]['type'] not in LUTS:continue
        occupied=exclusive_tiles(nl)|{(r['row'],r['column']) for r in nl['clusters'].values()};anchor=tuple(change['destination'])
        free=[(r,c) for r in range(device.rows) for c in range(device.cols) if device.grid[r][c]==17 and (r,c) not in occupied]
        if not free:break
        site=min(free,key=lambda s:(abs(s[0]-anchor[0])+abs(s[1]-anchor[1]),s))
        try:dd,ll,cc=implement(nd,nl,cut,site,change['ordered_boundary'],change['variant']+'_batch',change['buffered'])
        except ValueError:continue
        nd,nl=dd,ll;selected.append(cc);roots.add(root);interiors.update(cut['interior'])
        if len(selected)>=maximum:break
    if not selected:raise ValueError('No remaining compatible wide cut batch')
    return nd,nl,dict(operator='wide_mapping_batch',cells=sorted(set().union(*(set(c['cells']) for c in selected))),destination=[0,0],neighbor_luts=sorted(roots),variant='wide_batch_'+str(maximum)+'_'+sha(source/'trials.json')[:10],individuals=selected,interface_cycles_changed=False)
