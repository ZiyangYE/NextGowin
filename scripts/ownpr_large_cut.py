"""Exact eight-input Shannon mapping across calibrated, ordinary CFUs.

Uses LUT4/5/6/7 cofactors plus an ordinary LUT4 mux, never a physical LUT8.
State, I/O and shared upstream logic are preserved. Full CEC, routing and
native min/max STA remain admission gates.
"""
import copy,hashlib,json
from collections import Counter
from analyze_eight_input_macros import inspect
from ownpr_multilevel_cut import Cuts
from ownpr_mux_macros import tree
from ownpr_general_wide_recompile import cofactor,leaf_pin_order
from ownpr_critical_chain_move import locations,exclusive_tiles
from ownpr_state_safety import protected_cells,active
from ownpr_global_timing_frontier import select_roots
from ownpr_bidir_timing import analyze


def native_leaf_slots(level,slot):
    from ownpr_mux_architecture import children
    if level==4:return [slot]
    return [s for typ,child_slot in children(level,slot) for s in native_leaf_slots(int(typ[-1]),child_slot)]


def recover_dead_logic(design,layout):
    """Keep every state input, observed value and complete live wide tree."""
    cells=design['modules']['top']['cells'];module=design['modules']['top'];comb={n for n,c in cells.items() if c['type'].startswith(('LUT','MUX2_LUT'))}
    driver={b:n for n,c in cells.items() for p,bs in c['connections'].items() if c['port_directions'][p]=='output' for b in bs}
    seeds={b for c in cells.values() if c['type'] not in {cells[n]['type'] for n in comb} for p,bs in c['connections'].items() if c['port_directions'][p]=='input' for b in bs}
    seeds.update(b for p in module['ports'].values() if p['direction']!='input' for b in p['bits'])
    protected=protected_cells(design)|{row['lut'] for row in layout['clusters'].values() if row.get('ff')}
    for n in protected:
        if n in cells:seeds.update(b for p,bs in cells[n]['connections'].items() if cells[n]['port_directions'][p]=='output' for b in bs)
    for row in module.get('netnames',{}).values():
        if any(k.lower() in ('keep','syn_keep') and active(v) for k,v in row.get('attributes',{}).items()):seeds.update(row['bits'])
    live=set();stack=list(seeds)
    def drain():
        while stack:
            n=driver.get(stack.pop())
            if n not in comb or n in live:continue
            live.add(n);stack.extend(b for p,bs in cells[n]['connections'].items() if cells[n]['port_directions'][p]=='input' for b in bs)
    drain()
    changed=True
    while changed:
        changed=False
        for macro in layout.get('wide_macros',[]):
            spec=tree(design,macro['root'],macro['root_slot']);members=set(spec['leaves'])|set(spec['muxes'])
            if members&live and macro['root'] not in live:stack.extend(cells[macro['root']]['connections']['O']);changed=True
        drain()
    dead=comb-live
    for n in dead:
        if layout.get('clusters',{}).get(n,{}).get('ff'):raise ValueError('Dead-cone recovery would remove a state anchor')
        del cells[n];layout.get('clusters',{}).pop(n,None);layout.get('mux_clusters',{}).pop(n,None)
    layout['wide_macros']=[m for m in layout.get('wide_macros',[]) if m['root'] in cells]
    return sorted(dead)


def exact_truth(design,root,boundary):
    graph=Cuts(design);boundary=tuple(boundary)
    if len(boundary)!=8 or len(set(boundary))!=8:raise ValueError('Expected eight distinct cut variables')
    full=(1<<256)-1;values={b:sum(((a>>i)&1)<<a for a in range(256)) for i,b in enumerate(boundary)};stack=set()
    def evaluate(bit):
        if bit in values:return values[bit]
        if bit in graph.constants:return full if graph.constants[bit]=='1' else 0
        name=graph.drivers.get(bit)
        if name not in graph.functions or name in stack:raise ValueError('Uncovered or cyclic cut')
        stack.add(name);inputs,_,tt=graph.functions[name];args=[evaluate(b) for b in inputs];result=0
        for assignment in range(1<<len(args)):
            if not (tt>>assignment)&1:continue
            term=full
            for i,value in enumerate(args):term &= value if (assignment>>i)&1 else full^value
            result|=term
        stack.remove(name);values[bit]=result;return result
    return evaluate(graph.functions[root][1])


def eligible_macro(design,layout,root):
    cells=design['modules']['top']['cells'];spec=tree(design,root);members=set(spec['leaves'])|set(spec['muxes'])
    if members&protected_cells(design):raise ValueError('Protected macro member')
    macro=next(m for m in layout['wide_macros'] if m['root']==root);site=macro['row'],macro['column']
    if any(row.get('ff') for row in layout['clusters'].values() if (row['row'],row['column'])==site):raise ValueError('State occupies the replaced macro CFU')
    observed={b for p in design['modules']['top']['ports'].values() for b in p['bits']}
    for row in design['modules']['top'].get('netnames',{}).values():
        if any(k.lower() in ('keep','syn_keep') and active(v) for k,v in row.get('attributes',{}).items()):observed.update(row['bits'])
    internal={b for n in members for p,bs in cells[n]['connections'].items() if cells[n]['port_directions'][p]=='output' for b in bs}
    if (internal-set(cells[root]['connections']['O']))&observed:raise ValueError('Observed internal macro signal')
    return spec,site,members


def select(design,layout,model,count=2):
    metrics=analyze(model['late'],model['setup_requirements']);rows=inspect(design,layout,model);eligible={};rejected=[]
    for row in rows:
        try:eligible_macro(design,layout,row['root']);eligible[row['root']]=row
        except ValueError as e:rejected.append({'root':row['root'],'reason':str(e)})
    ranked,frontier=select_roots(design,layout,model['late'],metrics,eligible,limit=max(1,len(eligible)))
    chosen=[];serial=[]
    for root in ranked:
        if any(root in eligible[n]['upstream_cells'] or n in eligible[root]['upstream_cells'] for n in chosen):serial.append(root);continue
        chosen.append(root)
        if len(chosen)==count:break
    return [eligible[n] for n in chosen],metrics,dict(frontier=frontier,rejected=rejected,serial_roots_not_composed=serial)


def apply(design,layout,device,rows,metrics,select_rank=0,output_weight=8.):
    if not rows:raise ValueError('No eligible large cuts')
    new=copy.deepcopy(design);placed=copy.deepcopy(layout);old=design['modules']['top']['cells'];cells=new['modules']['top']['cells'];points=locations(layout)
    drivers={b:n+'/'+p for n,c in old.items() for p,bs in c['connections'].items() if c['port_directions'][p]=='output' for b in bs}
    allbits=[b for c in old.values() for bs in c['connections'].values() for b in bs]+[b for x in list(new['modules']['top'].get('netnames',{}).values())+list(new['modules']['top']['ports'].values()) for b in x['bits']]
    counter=max(b for b in allbits if isinstance(b,int))+1
    constants={c['type']:next(iter(c['connections'].values()))[0] for c in cells.values() if c['type'] in ('GND','VCC')}
    if set(constants)!={'GND','VCC'}:raise ValueError('Real constant drivers required')
    ground=constants['GND'];requests={};cofactors={};history=[];removed=[];added=[];selectors={}
    def allocate():
        nonlocal counter
        b=counter;counter+=1;return b
    def score(bit):
        p=drivers.get(bit);slack=min(metrics['slack'].get(p,[0.]));arrival=max(metrics['arrival'].get(p,[0.]));return slack,-arrival,bit
    def add(name,typ,pins,word=None,attrs=None):
        if name in cells:raise ValueError('Large-cut namespace collision')
        cells[name]={'hide_name':0,'type':typ,'parameters':{} if word is None else {'INIT':format(word,'016b')},'attributes':attrs or {},
                     'connections':{p:[b] for p,b in pins.items()},'port_directions':{p:'output' if p in ('F','O') else 'input' for p in pins}}
        added.append(name)
    for row in rows:
        root=row['root'];boundary=tuple(row['cut_bits']);truth=exact_truth(design,root,boundary)
        if truth!=int(row['truth_hex'],16):raise ValueError('Changed cut truth certificate')
        spec,site,members=eligible_macro(design,layout,root);late=sorted(boundary,key=score)
        chosen=late[select_rank%len(late)];branches=[]
        for value in (0,1):
            bits,word=cofactor(boundary,truth,chosen,value)
            if not bits:branches.append((constants['VCC' if word else 'GND'],False));continue
            mask=(1<<(1<<len(bits)))-1;canonical=min(word,mask^word);flip=word!=canonical;key=(bits,canonical)
            if key not in requests:
                tag=hashlib.sha256(repr(key).encode()).hexdigest()[:16];name='ng_large_cofactor_'+tag
                requests[key]={'root':name,'bits':bits,'truth':canonical,'output':allocate(),'sinks':[]}
            requests[key]['sinks'].append(site);branches.append((requests[key]['output'],flip))
        output=old[root]['connections']['O'][0];attrs=copy.deepcopy(old[root].get('attributes',{}))
        for n in members:
            del cells[n];placed.get('clusters',{}).pop(n,None);placed.get('mux_clusters',{}).pop(n,None);removed.append(n)
        placed['wide_macros']=[m for m in placed['wide_macros'] if m['root']!=root]
        occupied={r['slot'] for r in placed['clusters'].values() if (r['row'],r['column'])==site}
        slot=next((s for s in (3,2,1,0,7,6,5,4) if s not in occupied),None)
        if slot is None:raise ValueError('No selector slot in released root CFU')
        word=sum((((a>>2)&1)^branches[1][1] if (a>>3)&1 else ((a>>1)&1)^branches[0][1])<<a for a in range(16))
        add(root,'LUT4',{'I0':ground,'I1':branches[0][0],'I2':branches[1][0],'I3':chosen,'F':output},word,attrs)
        placed['clusters'][root]={'lut':root,'ff':None,'row':site[0],'column':site[1],'slot':slot}
        selectors[root]={'select_bit':chosen,'branches':branches,'site':list(site),'slot':slot}
        history.append({'root':root,'cut_bits':list(boundary),'truth_hex':row['truth_hex'],'select_bit':chosen,'old_members':sorted(members),'upstream_logic_retained':True})
    occupied=exclusive_tiles(placed)|{(r['row'],r['column']) for r in placed['clusters'].values()}
    free={(r,c) for r in range(device.rows) for c in range(device.cols) if device.grid[r][c]==17 and (r,c) not in occupied}
    for key,request in requests.items():
        bits,truth=request['bits'],request['truth'];width=len(bits);order=sorted(bits,key=lambda b:(-score(b)[0],-score(b)[1],b))
        input_points=[points.get(drivers[b].rsplit('/',1)[0]) for b in bits]
        def cost(site):return sum(abs(site[0]-p[0])+abs(site[1]-p[1]) for p in input_points if p)+output_weight*sum(abs(site[0]-p[0])+abs(site[1]-p[1]) for p in request['sinks'])
        if not free:raise ValueError('No vacant type17 CFU for a cofactor')
        site=min(free,key=lambda p:(cost(p),p));free.remove(site);r,c=site
        ordered=0
        for a in range(1<<width):
            vals={b:(a>>i)&1 for i,b in enumerate(order)};address=sum(vals[b]<<i for i,b in enumerate(bits));ordered|=((truth>>address)&1)<<a
        def leaf(name,word,data,slot,output):
            used=[i for i in range(len(data)) if any(((word>>a)^(word>>(a^(1<<i))))&1 for a in range(1<<len(data)))]
            routed=sorted([data[i] for i in used],key=score) or [order[0]];pins=[ground]*4
            for pin,b in zip(leaf_pin_order(slot),routed):pins[pin]=b
            init=0
            for a in range(16):
                vals={b:(a>>i)&1 for i,b in enumerate(pins) if b!=ground}
                address=sum(vals.get(b,0)<<i for i,b in enumerate(data));init|=((word>>address)&1)<<a
            add(name,'LUT4',{**{f'I{i}':pins[i] for i in range(4)},'F':output},init)
            placed['clusters'][name]={'lut':name,'ff':None,'row':r,'column':c,'slot':slot}
        if width<=4:
            leaf(request['root'],ordered,order,3,request['output'])
        else:
            current=[]
            slots=native_leaf_slots(width,{5:0,6:1,7:3}[width])
            for i in range(1<<(width-4)):
                name=request['root']+'_leaf_'+str(i);out=allocate();leaf(name,(ordered>>(16*i))&65535,order[:4],slots[i],out);current.append(out)
            for level in range(5,width+1):
                following=[]
                for i in range(len(current)//2):
                    last=level==width;name=request['root'] if last else request['root']+f'_mux_{level}_{i}';out=request['output'] if last else allocate()
                    add(name,'MUX2_LUT'+str(level),{'I0':current[2*i],'I1':current[2*i+1],'S0':order[level-1],'O':out});following.append(out)
                current=following
            spec=tree(new,request['root']);placed['wide_macros'].append({'root':request['root'],'row':r,'column':c,'root_slot':{5:0,6:1,7:3}[width]})
            for n,s in spec['muxes'].items():placed['mux_clusters'][n]={'row':r,'column':c,**s}
        cofactors[request['root']]={'bits':list(bits),'truth_hex':hex(truth),'site':list(site),'logical_width':width,'sinks':request['sinks']}
    for row in rows:
        if exact_truth(new,row['root'],row['cut_bits'])!=int(row['truth_hex'],16):raise ValueError('Large-cut reconstruction changed the actual function')
    dead=recover_dead_logic(new,placed)
    # Re-place only the newly created cofactor groups after live-area recovery.
    # Old FF/IO and surviving old macros remain fixed.
    moving={}
    for name,request in [(r['root'],r) for r in requests.values() if r['root'] in cells]:
        if cells[name]['type'].startswith('MUX2_LUT'):
            spec=tree(new,name);moving[name]=set(spec['leaves'])|set(spec['muxes'])
        else:moving[name]={name}
    moving_cells=set().union(*moving.values()) if moving else set()
    fixed={(r['row'],r['column']) for n,r in placed['clusters'].items() if n not in moving_cells}
    fixed.update((r['row'],r['column']) for n,r in placed.get('mux_clusters',{}).items() if n not in moving_cells)
    fixed.update((r['row'],r['column']) for r in placed.get('arithmetic_clusters',{}).values())
    fixed.update((r['row'],r['column']+i) for r in placed.get('hard_clusters',{}).values() for i in range(3))
    free={(r,c) for r in range(device.rows) for c in range(device.cols) if device.grid[r][c]==17 and (r,c) not in fixed}
    for request in requests.values():
        name=request['root']
        if name not in moving:continue
        input_points=[points.get(drivers[b].rsplit('/',1)[0]) for b in request['bits']]
        sinks=[p for p in request['sinks'] if any(root in cells and tuple(v['site'])==tuple(p) for root,v in selectors.items())]
        def final_cost(site):return sum(abs(site[0]-p[0])+abs(site[1]-p[1]) for p in input_points if p)+output_weight*sum(abs(site[0]-p[0])+abs(site[1]-p[1]) for p in sinks)
        site=min(free,key=lambda p:(final_cost(p),p));free.remove(site)
        for n in moving[name]:
            loc=placed['clusters'].get(n) or placed['mux_clusters'][n];loc.update(row=site[0],column=site[1])
        for macro in placed['wide_macros']:
            if macro['root']==name:macro.update(row=site[0],column=site[1])
        cofactors[name]['site']=list(site)
    before_ff={n:c for n,c in old.items() if not (c['type'].startswith('LUT') or c['type'].startswith('MUX2_LUT'))}
    after_ff={n:c for n,c in cells.items() if not (c['type'].startswith('LUT') or c['type'].startswith('MUX2_LUT'))}
    if before_ff!=after_ff:raise ValueError('Changed state/hard/IO boundary')
    placed.pop('weighted_internal_manhattan',None);placed.update(physical_legality_complete=False,timing_signoff=False)
    return new,placed,{'operator':'eight_input_multicfu_shannon','roots':history,'selectors':selectors,'cofactors':cofactors,'removed_cells':removed,'added_cells':added,
        'lut_delta':sum(c['type'].startswith('LUT') for c in cells.values())-sum(c['type'].startswith('LUT') for c in old.values()),
        'recovered_dead_cells':dead,'active_added_cells':[n for n in added if n in cells],
        'register_delta':0,'interface_cycles_changed':False,'clock_endpoints_unchanged':True,'physical_lut8_used':False,'cofactor_output_weight':output_weight}
