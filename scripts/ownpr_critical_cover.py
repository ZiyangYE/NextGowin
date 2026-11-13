"""Coordinated exact-copy proposals covering a complete critical pin DAG.

Inspired by FAPO's restricted all-critical-path covering condition, not its
implementation or QoR claim. Rise/fall vertices are conservatively merged by
pin. A cut proves coverage in this overapproximation, never delay improvement.
Actual shared-fanin loads, legal placement, CEC and native STA remain gates.
"""
import copy,hashlib,json,math
from collections import defaultdict,deque
from ownpr_lut_graph_feedback import LUTS
from ownpr_critical_chain_move import locations,exclusive_tiles
from ownpr_state_safety import protected_cells
from ownpr_shared_dag import connectivity


def minimum_cover(vertices,edges,sources,sinks,eligible):
    vertices=set(vertices);eligible=set(eligible)&vertices
    if not sources or not sinks or not set(sources)<=vertices or not set(sinks)<=vertices:raise ValueError('Missing critical boundary')
    infinity=len(eligible)+1;capacity=defaultdict(dict);start=('<source>',0);end=('<sink>',1)
    def add(u,v,w):
        capacity[u][v]=capacity[u].get(v,0)+w;capacity[v].setdefault(u,0)
    for n in vertices:add((n,0),(n,1),1 if n in eligible else infinity)
    for u,v in set(edges):
        if u not in vertices or v not in vertices:raise ValueError('Unknown critical edge')
        add((u,1),(v,0),infinity)
    for n in sources:add(start,(n,0),infinity)
    for n in sinks:add((n,1),end,infinity)
    flow=0
    while flow<infinity:
        parent={start:None};queue=deque([start])
        while queue and end not in parent:
            u=queue.popleft()
            for v,w in capacity[u].items():
                if w>0 and v not in parent:parent[v]=u;queue.append(v)
        if end not in parent:break
        amount=infinity-flow;v=end
        while v!=start:amount=min(amount,capacity[parent[v]][v]);v=parent[v]
        v=end
        while v!=start:
            u=parent[v];capacity[u][v]-=amount;capacity[v][u]+=amount;v=u
        flow+=amount
    if flow>=infinity:return None
    reachable={start};queue=deque([start])
    while queue:
        u=queue.popleft()
        for v,w in capacity[u].items():
            if w>0 and v not in reachable:reachable.add(v);queue.append(v)
    cut=sorted(n for n in eligible if (n,0) in reachable and (n,1) not in reachable)
    if len(cut)!=flow:raise ValueError('Non-unit critical cover')
    # Independent deletion reachability must disconnect EVERY critical path.
    if not covers(edges,sources,sinks,cut):raise ValueError('Incomplete critical cut')
    return cut


def covers(edges,sources,sinks,cut):
    removed=set(cut);nexts=defaultdict(list)
    for u,v in edges:
        if u not in removed and v not in removed:nexts[u].append(v)
    seen=set(sources)-removed;queue=deque(seen)
    while queue:
        for v in nexts[queue.popleft()]:
            if v not in seen:seen.add(v);queue.append(v)
    return not seen&set(sinks)


def critical_graph(graph,metrics,window):
    threshold=metrics['wns']+window;edges=set();wire=defaultdict(set)
    for e in graph['edges']:
        values=[metrics['required'][e['destination']][b]-metrics['arrival'][e['source']][a]-e['delay'][b] for a,b in e['transitions']]
        if any(math.isfinite(v) and v<=threshold+1e-8 for v in values):
            edges.add((e['source'],e['destination']))
            if e['kind']=='wire':wire[e['source']].add(e['destination'])
    sources={n for n in graph['seeds'] if min(metrics['slack'][n])<=threshold+1e-8}
    sinks={n for n,v in metrics['endpoints'].items() if v<=threshold+1e-8}
    vertices={n for edge in edges for n in edge}|sources|sinks
    return vertices,edges,sources,sinks,wire


def partition_copy(design,layout,root,receivers,site,tag):
    cells=design['modules']['top']['cells'];old=cells[root];bit=old['connections']['F'][0]
    if old['type'] not in LUTS or root in protected_cells(design):raise ValueError('Uncopyable root')
    _,loads=connectivity(design);wanted=set(receivers)
    if not wanted or not wanted<set(loads[bit]):raise ValueError('Copy requires two nonempty disjoint sink sets')
    occupied={(r['row'],r['column'],r['slot']) for r in layout['clusters'].values()}
    if tuple(site) in occupied or tuple(site[:2]) in exclusive_tiles(layout):raise ValueError('Occupied copy destination')
    new=copy.deepcopy(design);placed=copy.deepcopy(layout);cc=new['modules']['top']['cells'];name='ng_cover_'+tag+'_'+root
    if name in cc:raise ValueError('Copy collision')
    values=[b for c in cells.values() for bs in c['connections'].values() for b in bs]
    values.extend(b for r in design['modules']['top'].get('netnames',{}).values() for b in r['bits'])
    values.extend(b for r in design['modules']['top']['ports'].values() for b in r['bits'])
    output=1+max(b for b in values if type(b) is int);cc[name]=copy.deepcopy(old);cc[name]['connections']['F']=[output]
    for n,p,i in wanted:
        if not (cells[n]['type'] in LUTS or cells[n]['type'].startswith('MUX2_LUT') and p=='S0') or p not in cells[n]['connections'] or cc[n]['connections'][p][i]!=bit:raise ValueError('Unsupported or changed copy receiver')
        cc[n]['connections'][p][i]=output
    placed['clusters'][name]=dict(lut=name,ff=None,row=site[0],column=site[1],slot=site[2])
    return new,placed,dict(root=root,copy=name,site=list(site),receivers=sorted(wanted))


def compose_copies(design,layout,actions):
    """Materialize all copies, then reconnect actual critical copy consumers.

    Original selected roots serve their retained noncritical receivers. A
    selected child receives a selected parent's new source in the CHILD COPY,
    independent of root names or iteration order.
    """
    if len({x['root'] for x in actions})!=len(actions):raise ValueError('Duplicate copy action')
    new,placed=copy.deepcopy(design),copy.deepcopy(layout);changes=[];origins={}
    for action in actions:
        partial,placed,change=partition_copy(new,placed,action['root'],action['receivers'],action['site'],action['tag'])
        # Preserve copy creation, but defer every receiver mutation.
        clone=change['copy'];new['modules']['top']['cells'][clone]=partial['modules']['top']['cells'][clone]
        origins[clone]=action['root'];changes.append(change)
    by_root={x['root']:x for x in changes};cells=new['modules']['top']['cells'];old=design['modules']['top']['cells']
    for change in changes:
        output=cells[change['copy']]['connections']['F']
        for n,p,i in change['receivers']:
            receiver=by_root[n]['copy'] if n in by_root else n
            if cells[receiver]['connections'][p][i]!=old[change['root']]['connections']['F'][0]:raise ValueError('Atomic copy target was already changed')
            cells[receiver]['connections'][p][i]=output[0]
    return new,placed,changes,origins


def proposals(design,layout,device,metrics,graphs,limit=24,audit=None):
    cells=design['modules']['top']['cells'];points=locations(layout);excluded=exclusive_tiles(layout);protected=protected_cells(design);_,loads=connectivity(design)
    from ownpr_mux_macros import tree
    private={n for m in layout.get('wide_macros',[]) for n in tree(design,m['root'],m.get('root_slot'))['leaves']}
    excluded.update((row['row'],row['column']) for row in layout['clusters'].values() if row.get('ff') and not row.get('wide_ff_root') and row['slot'] not in (0,2,4))
    occupied={(r['row'],r['column'],r['slot']) for r in layout['clusters'].values()};rows=[];records=[]
    for window in (0.,.1,.3,.6):
        vertices,edges,sources,sinks,wire=critical_graph(graphs['setup'],metrics['setup'],window);eligible={};rejections=[]
        for n,c in cells.items():
            output=n+'/F'
            if c['type'] not in LUTS or n in protected or output not in vertices or points[n] in excluded:continue
            critical=wire.get(output,set());receivers=[r for r in loads[c['connections']['F'][0]] if r[0]+'/'+r[1] in critical]
            if receivers and set(receivers)<set(loads[c['connections']['F'][0]]) and all(x not in protected and (cells[x]['type'].startswith('MUX2_LUT') and p=='S0' or cells[x]['type'] in LUTS and (points[x] not in excluded or x in private)) for x,p,i in receivers):eligible[output]=receivers
        cut=minimum_cover(vertices,edges,sources,sinks,set(eligible))
        record=dict(window_ns=window,critical_pins=len(vertices),critical_edges=len(edges),sources=len(sources),endpoints=len(sinks),eligible_copy_sites=len(eligible),cut=cut,coverage_proved=cut is not None)
        records.append(record)
        if not cut:continue
        # All cut actions are composed atomically; no single-path pilot score.
        for variant in range(2):
            nd,nl=design,layout;actions=[];used=set(occupied)
            try:
                for pin in cut:
                    n=pin.rsplit('/',1)[0];receivers=eligible[pin];coordinates=[points[x] for x,p,i in receivers];center=tuple(sorted(p[k] for p in coordinates)[len(coordinates)//2] for k in (0,1))
                    candidates=[(r,c,s) for r in range(max(0,center[0]-2),min(device.rows,center[0]+3)) for c in range(max(0,center[1]-2),min(device.cols,center[1]+3)) if device.grid[r][c]==17 and (r,c) not in excluded for s in range(8) if (r,c,s) not in used]
                    candidates.sort(key=lambda p:(sum(abs(p[k]-q[k]) for q in coordinates for k in (0,1)),p[2]>=6,p))
                    if len(candidates)<=variant:raise ValueError('No legal cover copy site')
                    site=candidates[variant];used.add(site);tag=hashlib.sha256(json.dumps([n,site,receivers]).encode()).hexdigest()[:12]
                    actions.append(dict(root=n,receivers=receivers,site=site,tag=tag))
                nd,nl,changes,origins=compose_copies(design,layout,actions)
                tag=hashlib.sha256(json.dumps(changes,sort_keys=True).encode()).hexdigest()[:14]
                change=dict(operator='critical_cover',cells=[x['root'] for x in changes],copies=changes,input_origins=origins,destination=changes[0]['site'],neighbor_luts=sorted(x['copy'] for x in changes),variant=tag,coverage=record,interface_cycles_changed=False)
                rows.append((metrics['setup']['wns'],-len(cut),'__critical_cover_'+tag,nl,change,nd))
            except ValueError as e:rejections.append(str(e))
        record['proposal_errors']=rejections
    if audit is not None:audit.update(records=records,scope='Minimum unit-copy cover of conservative pin-merged critical graph; candidate coverage, not physical speedup or global optimum.',offered_candidates=len(rows))
    return rows[:limit]
