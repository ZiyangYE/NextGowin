"""Measured local wide-macro moves with every register and clock fixed.

Full-graph timing ranks incident edges. Geometry/density is a proposal prior;
loaded routing and complete setup/hold graphs decide each transaction.
"""
import copy,hashlib,json,math,time
from collections import defaultdict
from ownpr_cross_macro_recompile import CrossMacroRecompileFeedback
from ownpr_general_wide_recompile import info
from ownpr_mux_macros import validate as validate_mux,tree
from ownpr_critical_chain_move import locations
from ownpr_state_safety import protected_cells


def movable(design,layout):
    result={};protected=protected_cells(design)
    for macro in layout.get('wide_macros',[]):
        root=macro['root'];spec=tree(design,root);leaves=set(spec['leaves']);members=leaves|set(spec['muxes']);point=macro['row'],macro['column']
        occupants={n:r for n,r in layout['clusters'].items() if (r['row'],r['column'])==point}
        if set(occupants)!=leaves or any(r.get('ff') for r in occupants.values()) or members&protected:continue
        result[root]=(spec,members)
    return result


def movable_atoms(design,layout,include_ordinary=False):
    result=movable(design,layout)
    if not include_ordinary:return result
    protected=protected_cells(design);excluded={(m['row'],m['column']) for m in layout.get('wide_macros',[])}
    excluded.update((r['row'],r['column']) for r in layout.get('arithmetic_clusters',{}).values())
    excluded.update((r['row'],r['column']+i) for r in layout.get('hard_clusters',{}).values() for i in range(3))
    occupants=defaultdict(list)
    for n,r in layout['clusters'].items():occupants[r['row'],r['column']].append(n)
    for n,r in layout['clusters'].items():
        point=r['row'],r['column']
        if r.get('ff') or n in protected or point in excluded or occupants[point]!=[n]:continue
        if design['modules']['top']['cells'][n]['type'] not in ('LUT1','LUT2','LUT3','LUT4'):continue
        result[n]=(dict(root=n,level=4,leaves={n:r['slot']},muxes={}),{n})
    return result


class WideTrustRegionFeedback(CrossMacroRecompileFeedback):
    def admit(self,design,layout,static):
        normalized=copy.deepcopy(layout);old={m['root']:m for m in self.layout.get('wide_macros',[])};new={m['root']:m for m in layout.get('wide_macros',[])}
        if set(new)!=set(old) or len(new)!=len(layout.get('wide_macros',[])):raise ValueError('Changed macro membership')
        allowed=movable(self.reference,self.layout);moved=set();back={}
        occupied={(r['row'],r['column']) for r in layout.get('arithmetic_clusters',{}).values()}
        occupied.update((r['row'],r['column']+i) for r in layout.get('hard_clusters',{}).values() for i in range(3))
        targets=[]
        for root,m in new.items():
            prior=old[root];point=(m['row'],m['column']);previous=(prior['row'],prior['column'])
            if point==previous:continue
            if root not in allowed:raise ValueError('Moved register, protected or shared CFU')
            spec,members=allowed[root]
            if point in occupied or any((r['row'],r['column'])==point and n not in spec['leaves'] for n,r in layout['clusters'].items()):raise ValueError('Destination is occupied in final placement')
            if sum(abs(a-b) for a,b in zip(point,previous))>getattr(self,'trust_radius',6):raise ValueError('Exceeded reference-anchored CFU trust region')
            if any(type(v) is not int or v<0 for v in point) or point[0]>=len(self.device.grid) or point[1]>=len(self.device.grid[point[0]]) or self.device.grid[point[0]][point[1]]!=17:raise ValueError('Uncalibrated moved CFU')
            targets.append(point);moved.update(members)
            for n in members:
                key='clusters' if n in spec['leaves'] else 'mux_clusters';row=layout[key][n];was=self.layout[key][n]
                if (row['row'],row['column'])!=point or {k:v for k,v in row.items() if k not in ('row','column')}!={k:v for k,v in was.items() if k not in ('row','column')}:raise ValueError('Macro did not translate rigidly')
                normalized[key][n]=copy.deepcopy(was);back[n]=previous
            next(x for x in normalized['wide_macros'] if x['root']==root).update(row=prior['row'],column=prior['column'])
        ordinary=movable_atoms(self.reference,self.layout,True)
        wide_members=set().union(*(set(spec['leaves'])|set(spec['muxes']) for spec,_ in allowed.values())) if allowed else set()
        for n,row in layout['clusters'].items():
            prior=self.layout['clusters'].get(n)
            if prior is None or n in wide_members or row==prior:continue
            if not getattr(self,'ordinary_group_moves',False) or n not in ordinary or ordinary[n][0]['level']!=4:raise ValueError('Ordinary LUT movement not admitted')
            if design['modules']['top']['cells'][n]!=self.reference['modules']['top']['cells'][n]:raise ValueError('Group ordinary LUT function changed')
            if {k:v for k,v in row.items() if k not in ('row','column')}!={k:v for k,v in prior.items() if k not in ('row','column')}:raise ValueError('Ordinary LUT slot/control changed')
            point=row['row'],row['column']
            if not within_reference_region(self,n,point) or point in occupied:raise ValueError('Ordinary LUT destination outside admitted region')
            if any(type(v) is not int or v<0 for v in point) or point[0]>=len(self.device.grid) or point[1]>=len(self.device.grid[point[0]]) or self.device.grid[point[0]][point[1]]!=17:raise ValueError('Uncalibrated ordinary LUT destination')
            if any((r['row'],r['column'])==point and other!=n for other,r in layout['clusters'].items()):raise ValueError('Ordinary LUT destination occupied')
            normalized['clusters'][n]=copy.deepcopy(prior);targets.append(point)
        if len(set(targets))!=len(targets):raise ValueError('Moved macros overlap')
        # Delegate immutable state/interface and exact global truth checks at
        # reference geometry, then independently validate actual new geometry.
        normalized_static=copy.deepcopy(static)
        for row in normalized_static:
            if row.get('kind')=='mux_dedicated_link' and row['destination'] in back:row['row'],row['column']=back[row['destination']]
        super().admit(design,normalized,normalized_static)
        checked=validate_mux(design,layout,self.device)
        wanted={(r['source'],r['destination'],r['pin'],r['row'],r['column'],r['source_slot'],r['destination_slot']) for r in checked['links']}
        got={(r['source'],r['destination'],r['pin'],r['row'],r['column'],r['source_slot'],r['destination_slot']) for r in static if r.get('kind')=='mux_dedicated_link'}
        if wanted!=got:raise ValueError('Translated dedicated links differ')
        return checked


def translate(layout,spec,root,point):
    result=copy.deepcopy(layout)
    for n in spec['leaves']:result['clusters'][n].update(row=point[0],column=point[1])
    for n in spec['muxes']:result['mux_clusters'][n].update(row=point[0],column=point[1])
    next(m for m in result['wide_macros'] if m['root']==root).update(row=point[0],column=point[1])
    return result


def within_reference_region(feedback,root,point):
    source=next((m for m in feedback.layout['wide_macros'] if m['root']==root),None)
    if source is None:source=feedback.layout['clusters'][root]
    return abs(point[0]-source['row'])+abs(point[1]-source['column'])<=getattr(feedback,'trust_radius',6)


def translate_group(layout,allowed,targets):
    if len(targets)<2 or len(set(targets.values()))!=len(targets):raise ValueError('Group needs distinct destinations')
    result=copy.deepcopy(layout)
    for root,point in targets.items():
        spec,_=allowed[root]
        for n in spec['leaves']:result['clusters'][n].update(row=point[0],column=point[1])
        for n in spec['muxes']:result['mux_clusters'][n].update(row=point[0],column=point[1])
        if spec['muxes']:next(m for m in result['wide_macros'] if m['root']==root).update(row=point[0],column=point[1])
    return result


def group_proposals(design,layout,device,metrics,graphs,feedback,limit=16,deadline=None):
    """Move connected pairs/triples together; exact routed timing remains decisive."""
    allowed=movable_atoms(design,layout,getattr(feedback,'ordinary_group_moves',False));points=locations(layout)
    owner={n:root for root,(_,members) in allowed.items() for n in members}
    graph=graphs['setup'];slacks=metrics['setup']['slack'];wns=metrics['setup']['wns'];links={}
    for e in graph['edges']:
        if e['kind']!='wire':continue
        a,b=(e[k].rsplit('/',1)[0] for k in ('source','destination'));ra,rb=owner.get(a),owner.get(b)
        if ra and rb and ra!=rb:
            key=tuple(sorted((ra,rb)));score=(min(slacks[e['destination']]),-max(e['delay']))
            links[key]=min(links.get(key,score),score)
    groups=[frozenset(pair) for pair in sorted(links,key=lambda pair:(links[pair],pair))[:16]]
    for first in list(groups[:8]):
        options=[second for second in groups if len(first&second)==1 and len(first|second)==3]
        if options:groups.append(first|options[0])
    groups=list(dict.fromkeys(groups));occupied=set(points.values())
    occupied.update((r['row'],r['column']+i) for r in layout.get('hard_clusters',{}).values() for i in range(3))
    rows=[];queues={};radius=getattr(feedback,'trust_radius',6)
    for members in groups:
        if deadline is not None and time.monotonic()>deadline:break
        roots=sorted(members);cells=set().union(*(allowed[r][1] for r in roots));origins={r:points[r] for r in roots}
        external=occupied-set(origins.values());edges=[]
        for e in graph['edges']:
            if e['kind']!='wire':continue
            a,b=(e[k].rsplit('/',1)[0] for k in ('source','destination'))
            if a not in cells and b not in cells or a not in points or b not in points:continue
            weight=1.+39.*math.exp(max(-20.,min(0.,(wns-min(slacks[e['destination']]))/.5)))
            edges.append((a,b,weight))
        def cost(targets):
            total=0.
            for a,b,w in edges:
                pa=targets.get(owner.get(a),points[a]);pb=targets.get(owner.get(b),points[b])
                total+=w*(abs(pa[0]-pb[0])+abs(pa[1]-pb[1]))
            return total
        def legal(targets):
            return len(set(targets.values()))==len(targets) and all(0<=p[0]<device.rows and 0<=p[1]<device.cols and device.grid[p[0]][p[1]]==17 and p not in external and within_reference_region(feedback,r,p) for r,p in targets.items())
        initial=cost(origins);options={}
        for dr in range(-radius,radius+1):
            for dc in range(-radius,radius+1):
                if not 0<abs(dr)+abs(dc)<=radius:continue
                targets={r:(p[0]+dr,p[1]+dc) for r,p in origins.items()}
                if not legal(targets):continue
                gain=initial-cost(targets)
                if gain>0:options[tuple(targets[r] for r in roots)]=(gain,targets,'rigid_group')
        # Also contract related macros toward a common center. Each macro keeps
        # its own native internal geometry; relative macro distances may change.
        center=tuple(round(sum(p[i] for p in origins.values())/len(roots)) for i in (0,1))
        for dr,dc in ((0,0),(-2,0),(2,0),(0,-2),(0,2)):
            targets={};taken=set();anchor=center[0]+dr,center[1]+dc
            for root in roots:
                old=origins[root];sites=[]
                for r in range(max(0,old[0]-radius),min(device.rows,old[0]+radius+1)):
                    for c in range(max(0,old[1]-radius),min(device.cols,old[1]+radius+1)):
                        point=r,c
                        if point in external or point in taken or device.grid[r][c]!=17 or not within_reference_region(feedback,root,point):continue
                        sites.append(point)
                if not sites:break
                point=min(sites,key=lambda p:(abs(p[0]-anchor[0])+abs(p[1]-anchor[1]),abs(p[0]-old[0])+abs(p[1]-old[1]),p));targets[root]=point;taken.add(point)
            if len(targets)!=len(roots) or not legal(targets):continue
            gain=initial-cost(targets)
            if gain>0:options[tuple(targets[r] for r in roots)]=(gain,targets,'contracted_group')
        for gain,targets,kind in sorted(options.values(),key=lambda x:(-x[0],tuple(x[1][r] for r in roots)))[:2]:
            nl=translate_group(layout,allowed,targets);tag=hashlib.sha256(json.dumps([roots,targets],sort_keys=True).encode()).hexdigest()[:14]
            change=dict(operator='wide_trust_region_group',root=roots[0],roots=roots,cells=sorted(cells),destination=list(targets[roots[0]]),
                neighbor_luts=sorted(n for r in roots for n in allowed[r][0]['leaves']),variant=tag,group_move_kind=kind,
                individuals=[dict(root=r,previous=list(origins[r]),destination=list(targets[r])) for r in roots],
                full_graph_weighted_distance_gain=gain,registers_and_clocks_fixed=True,interface_cycles_changed=False)
            row=(min(min(slacks[r+('/O' if allowed[r][0]['muxes'] else '/F')]) for r in roots),-gain,'__wide_group_'+tag,nl,change,design)
            queues.setdefault(tuple(roots),[]).append(row)
    while any(queues.values()) and len(rows)<limit:
        for group in queues:
            if queues[group] and len(rows)<limit:rows.append(queues[group].pop(0))
    return rows


def translate_recompile(design,layout,root,point,promoted,priority):
    """One exact logic/placement transaction; routing must remeasure all changed loads."""
    from ownpr_general_wide_recompile import apply
    nd,nl,change=apply(design,layout,root,promoted,priority=priority)
    spec=tree(nd,root);origin=next(m for m in layout['wide_macros'] if m['root']==root)
    nl=translate(nl,spec,root,point)
    change.update(operator='wide_trust_region',joint_mapping_and_move=True,previous=[origin['row'],origin['column']],destination=list(point),
        physical_positions_preserved=False,registers_and_clocks_fixed=True)
    change['variant']=hashlib.sha256(json.dumps([change['variant'],point]).encode()).hexdigest()[:14]
    return nd,nl,change


def proposals(design,layout,device,metrics,graphs,feedback,limit=24,audit=None,deadline=None):
    from ownpr_global_timing_frontier import select_roots
    allowed=movable(design,layout);points=locations(layout);occupied=set(points.values())
    occupied.update((r['row'],r['column']+i) for r in layout.get('hard_clusters',{}).values() for i in range(3))
    roots,coverage=select_roots(design,layout,graphs['setup'],metrics['setup'],allowed,limit=40);queues={};wns=metrics['setup']['wns'];radius=getattr(feedback,'trust_radius',6)
    for root in roots:
        spec,members=allowed[root];origin=points[root];incident=[]
        for edge in graphs['setup']['edges']:
            a=edge['source'].rsplit('/',1)[0];b=edge['destination'].rsplit('/',1)[0]
            if edge['kind']!='wire' or (a in members)==(b in members):continue
            other=b if a in members else a
            if other not in points:continue
            slack=min(metrics['setup']['slack'][edge['destination']]);weight=1.+39.*math.exp(max(-20.,min(0.,(wns-slack)/.5)))
            incident.append((points[other],weight))
        if not incident:continue
        def cost(point):return sum(w*(abs(point[0]-p[0])+abs(point[1]-p[1])) for p,w in incident)
        baseline=cost(origin);options=[]
        for r in range(max(0,origin[0]-radius),min(device.rows,origin[0]+radius+1)):
            for c in range(max(0,origin[1]-radius),min(device.cols,origin[1]+radius+1)):
                point=r,c
                if abs(r-origin[0])+abs(c-origin[1])>radius or point in occupied or device.grid[r][c]!=17:continue
                if not within_reference_region(feedback,root,point):continue
                density=sum(abs(r-p[0])+abs(c-p[1])<=2 for p in occupied)
                score=baseline-cost(point)-density*.1
                if score>0:options.append((-score,density,point))
        joint=[]
        if getattr(feedback,'joint_mapping_and_move',False):
            from ownpr_existing_wide_remap import macro_truth
            _,boundary,members=info(design,layout,root);support=macro_truth(design,root,boundary,members)['cut_bits']
            drivers={b:n+'/'+p for n,c in design['modules']['top']['cells'].items() for p,bs in c['connections'].items() if c['port_directions'][p]=='output' for b in bs}
            arrivals=metrics['setup']['arrival']
            known=[b for b in support if drivers.get(b) in arrivals and all(math.isfinite(v) for v in arrivals[drivers[b]])]
            if len(known)==len(support):
                # Proposal prior only: moving can change which input reaches the
                # macro last. Exact loaded routing decides acceptance below.
                for score,density,point in sorted(options)[:2]:
                    def arrival(bit):
                        source=drivers[bit].rsplit('/',1)[0];position=points.get(source,origin)
                        return max(arrivals[drivers[bit]])+.05*(abs(point[0]-position[0])+abs(point[1]-position[1]))
                    priority=sorted(support,key=lambda b:(-arrival(b),b))
                    for promoted in priority[:2]:
                        try:nd,nl,change=translate_recompile(design,layout,root,point,promoted,priority)
                        except ValueError:continue
                        change.update(full_graph_weighted_distance_gain=-score,local_occupied_cells=density,proposal_only_distance_ns_per_tile=.05)
                        joint.append((min(metrics['setup']['slack'][root+'/O']),score,root,nl,change,nd))
        queues.setdefault(root,[]).extend(joint)
        for score,density,point in sorted(options)[:2]:
            nl=translate(layout,spec,root,point);tag=hashlib.sha256(json.dumps([root,point]).encode()).hexdigest()[:14]
            change=dict(operator='wide_trust_region',root=root,cells=sorted(members),destination=list(point),neighbor_luts=sorted(spec['leaves']),variant=tag,
                        previous=list(origin),full_graph_weighted_distance_gain=-score,local_occupied_cells=density,registers_and_clocks_fixed=True,interface_cycles_changed=False)
            queues.setdefault(root,[]).append((min(metrics['setup']['slack'][root+'/O']),score,root,nl,change,design))
    rows=[]
    while any(queues.values()) and len(rows)<limit:
        for root in roots:
            if queues.get(root) and len(rows)<limit:rows.append(queues[root].pop(0))
    group_rows=group_proposals(design,layout,device,metrics,graphs,feedback,min(16,limit),deadline) if getattr(feedback,'group_moves',False) else []
    rows=(group_rows+rows)[:limit]
    if audit is not None:audit.update(coverage,movable_macros=len(allowed),offered_candidates=len(rows),group_move_candidates=len(group_rows),physical_candidates_measured=False,trust_region_l1=radius)
    return rows
