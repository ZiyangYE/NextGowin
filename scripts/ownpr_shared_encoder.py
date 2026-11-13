"""Exact shared-encoder ACD mapping with native CFU geometry.

Scores are proposals, not timing proofs. State and fixed I/O remain fixed;
complete CEC, own routing and native min/max timing decide admission.
"""
import copy
import hashlib
from collections import Counter
from acd_reference import evaluate
from analyze_large_cut_acd import candidates
from analyze_eight_input_macros import inspect
from register_control_map import normalize
from ownpr_large_cut import eligible_macro, exact_truth, native_leaf_slots, recover_dead_logic
from ownpr_general_wide_recompile import leaf_pin_order
from ownpr_mux_macros import tree
from ownpr_critical_chain_move import locations, exclusive_tiles
from ownpr_global_timing_frontier import select_roots
from ownpr_bidir_timing import analyze
from ownpr_multilevel_cut import Cuts


def canonical(inputs,truth):
    bits,word=normalize(inputs,truth);ordered=tuple(sorted(bits));result=0
    for a in range(1<<len(ordered)):
        values={b:(a>>i)&1 for i,b in enumerate(ordered)}
        result|=((word>>sum(values[b]<<i for i,b in enumerate(bits)))&1)<<a
    return ordered,result


def sink_frontier(eligible):
    return {n:r for n,r in eligible.items() if not any(n in s['upstream_cells'] for k,s in eligible.items() if k!=n)}


def exit_frontier(design,eligible):
    """Keep roots with a branch reaching a boundary without another eligible root.

    Unlike an antichain this admits an ancestor for a separate escaping branch.
    It is a coverage scheduler; it does not assert a timing improvement.
    """
    graph=Cuts(design);loads={};module=design['modules']['top']
    for n,c in graph.cells.items():
        for p,bs in c['connections'].items():
            if c['port_directions'][p]=='input':
                for b in bs:loads.setdefault(b,set()).add(n)
    observed={b for p in module['ports'].values() if p['direction']!='input' for b in p['bits']}
    memo={};active=set()
    def escapes(bit):
        if bit in memo:return memo[bit]
        if bit in active:raise ValueError('Cycle in exit frontier')
        if bit in observed or not loads.get(bit):return True
        active.add(bit);answer=False
        for n in loads[bit]:
            if n in eligible:continue
            if n not in graph.functions or escapes(graph.functions[n][1]):answer=True;break
        active.remove(bit);memo[bit]=answer;return answer
    return {n:r for n,r in eligible.items() if escapes(graph.functions[n][1])}


def select(design,layout,model,count=4,frontier_policy='sink'):
    metrics=analyze(model['late'],model['setup_requirements']);eligible={};rejected=[]
    for row in inspect(design,layout,model,require_full_support=False):
        try:eligible_macro(design,layout,row['root'])
        except ValueError as e:rejected.append(dict(root=row['root'],reason=str(e)));continue
        _,options=candidates(int(row['truth_hex'],16))
        if not options:_,options=candidates(int(row['truth_hex'],16),max_encoder=7)
        if options:eligible[row['root']]=dict(row,acd_options=options)
    if frontier_policy not in ('sink','exit'):raise ValueError('Invalid frontier policy')
    sink=exit_frontier(design,eligible) if frontier_policy=='exit' else sink_frontier(eligible)
    ranked,frontier=select_roots(design,layout,model['late'],metrics,sink,limit=count)
    return [sink[n] for n in ranked],metrics,dict(frontier=frontier,rejected=rejected,
        ancestor_roots_excluded=sorted(set(eligible)-set(sink)),frontier_policy=frontier_policy,
        scope='Eligible cut frontier; exit policy preserves branches escaping other eligible roots. Reachability is not timing improvement.')


def emit_function(design,layout,name,bits,truth,output,site,ground,allocate,priority,attributes=None,loads=None):
    """Emit one exact function, with normalized support and calibrated leaf order."""
    bits,truth=canonical(bits,truth)
    if len(bits)>7:raise ValueError('Native encoder function exceeds seven inputs')
    cells=design['modules']['top']['cells'];order=sorted(bits,key=priority,reverse=True)
    if loads is not None:
        # High-load sources should not be broadcast to every leaf in a wide macro.
        # This is a candidate ordering, not a calibrated wire-delay predictor.
        order=sorted(bits,key=lambda b:(loads.get(b,0),tuple(-v for v in priority(b))))
    # Critical/late inputs are last and occupy dedicated upper mux selectors.
    ordered=0
    for a in range(1<<len(order)):
        vals={b:(a>>i)&1 for i,b in enumerate(order)}
        ordered|=((truth>>sum(vals[b]<<i for i,b in enumerate(bits)))&1)<<a
    added=[]
    def add(n,typ,pins,word=None):
        if n in cells:raise ValueError('Encoder namespace collision')
        cells[n]=dict(hide_name=0,type=typ,attributes=copy.deepcopy(attributes or {}) if n==name else {},
            parameters={} if word is None else {'INIT':format(word,'016b')},
            connections={p:[b] for p,b in pins.items()},port_directions={p:'output' if p in ('F','O') else 'input' for p in pins})
        added.append(n)
    def leaf(n,word,data,slot,out):
        support,reduced=normalize(data,word);pins=[ground]*4
        # A physical constant leaf keeps a dynamic dummy input, as in existing calibrated mapping.
        routed=sorted(support,key=priority) or order[:1]
        if not routed:raise ValueError('Constant function requires a separately bound constant driver')
        for pin,b in zip(leaf_pin_order(slot),routed):pins[pin]=b
        init=0
        for a in range(16):
            vals={b:(a>>i)&1 for i,b in enumerate(pins) if b!=ground}
            init|=((reduced>>sum(vals[b]<<i for i,b in enumerate(support)))&1)<<a
        add(n,'LUT4',{**{f'I{i}':pins[i] for i in range(4)},'F':out},init)
        layout['clusters'][n]=dict(lut=n,ff=None,row=site[0],column=site[1],slot=slot)
    width=len(bits)
    if width<=4:leaf(name,ordered,order,3,output)
    else:
        current=[];root_slot={5:0,6:1,7:3}[width]
        for i,slot in enumerate(native_leaf_slots(width,root_slot)):
            out=allocate();leaf(name+'_leaf_'+str(i),(ordered>>(16*i))&65535,order[:4],slot,out);current.append(out)
        for level in range(5,width+1):
            following=[]
            for i in range(len(current)//2):
                last=level==width;n=name if last else name+f'_mux_{level}_{i}';out=output if last else allocate()
                add(n,'MUX2_LUT'+str(level),dict(I0=current[2*i],I1=current[2*i+1],S0=order[level-1],O=out));following.append(out)
            current=following
        spec=tree(design,name)
        layout['wide_macros'].append(dict(root=name,row=site[0],column=site[1],root_slot=root_slot))
        for n,s in spec['muxes'].items():layout['mux_clusters'][n]=dict(row=site[0],column=site[1],**s)
    return added


def apply(design,layout,device,rows,metrics,select_rank=0,output_weight=8.,joint=False,pin_policy='arrival'):
    if not rows:raise ValueError('No feasible shared-encoder candidates')
    new=copy.deepcopy(design);placed=copy.deepcopy(layout);old=design['modules']['top']['cells'];cells=new['modules']['top']['cells']
    points=locations(layout);drivers={b:n+'/'+p for n,c in old.items() for p,bs in c['connections'].items() if c['port_directions'][p]=='output' for b in bs}
    bits=[b for c in old.values() for bs in c['connections'].values() for b in bs]
    bits += [b for r in list(new['modules']['top']['ports'].values())+list(new['modules']['top'].get('netnames',{}).values()) for b in r['bits']]
    counter=max(b for b in bits if isinstance(b,int))+1
    def allocate():
        nonlocal counter
        b=counter;counter+=1;return b
    constants={c['type']:next(iter(c['connections'].values()))[0] for c in old.values() if c['type'] in ('GND','VCC')}
    if set(constants)!={'GND','VCC'}:raise ValueError('Missing constant drivers')
    if pin_policy not in ('arrival','load'):raise ValueError('Unsupported pin ordering')
    ground=constants['GND'];estimates={}
    input_loads=Counter(b for c in old.values() for pin,bs in c['connections'].items() if c['port_directions'][pin]=='input' for b in bs)
    def priority(bit):
        if bit in estimates:return estimates[bit]
        node=drivers.get(bit);return min(metrics['slack'].get(node,[0.])), -max(metrics['arrival'].get(node,[0.])),bit
    encoders={};plans=[];removed=[];added=[];joint_groups=[]
    choices={r['root']:r['acd_options'][select_rank%len(r['acd_options'])] for r in rows}
    if joint:
        from ownpr_multioutput_acd import joint_choices
        choices,joint_groups=joint_choices(rows,select_rank)
    for row in rows:
        root=row['root'];boundary=row['cut_bits'];truth=exact_truth(design,root,boundary)
        if truth!=int(row['truth_hex'],16):raise ValueError('Changed exact truth certificate')
        c=choices[root]
        if any(evaluate(c,a)!=((truth>>a)&1) for a in range(256)):raise ValueError('Invalid ACD certificate')
        spec,site,members=eligible_macro(design,layout,root);outputs=[];flips=[]
        for word in c['leaves']:
            support,word=canonical([boundary[i] for i in c['bound']+c['shared']],word)
            if not support:outputs.append(constants['VCC' if word else 'GND']);flips.append(False);continue
            mask=(1<<(1<<len(support)))-1;value=min(word,mask^word);key=support,value
            if key not in encoders:
                name='ng_acd_encoder_'+hashlib.sha256(repr(key).encode()).hexdigest()[:16];out=allocate()
                encoders[key]=dict(root=name,bits=support,truth=value,output=out,sinks=[])
                pr=min(priority(b) for b in support);estimates[out]=(pr[0]-0.8,pr[1]-0.8,out)
            encoders[key]['sinks'].append(site);outputs.append(encoders[key]['output']);flips.append(word!=value)
        root_bits=[boundary[i] for i in c['free']+c['shared']]+outputs
        flipmask=sum(int(v)<<(len(c['free'])+len(c['shared'])+i) for i,v in enumerate(flips))
        root_truth=sum(((c['root']>>(a^flipmask))&1)<<a for a in range(1<<len(root_bits)))
        root_bits=['0' if b==ground else '1' if b==constants['VCC'] else b for b in root_bits]
        root_bits,root_truth=canonical(root_bits,root_truth)
        plans.append(dict(root=root,bits=root_bits,truth=root_truth,output=old[root]['connections']['O'][0],site=site,certificate=c,cut_bits=boundary,source_truth=truth))
        for n in members:
            del cells[n];placed['clusters'].pop(n,None);placed['mux_clusters'].pop(n,None);removed.append(n)
        placed['wide_macros']=[m for m in placed['wide_macros'] if m['root']!=root]
    occupied=exclusive_tiles(placed)|{(r['row'],r['column']) for r in placed['clusters'].values()}
    free={(r,c) for r in range(device.rows) for c in range(device.cols) if device.grid[r][c]==17 and (r,c) not in occupied}
    for plan in plans:
        site=plan['site']
        if site not in free:site=min(free,key=lambda s:(abs(s[0]-site[0])+abs(s[1]-site[1]),s))
        free.remove(site);plan['placed_site']=site
        added+=emit_function(new,placed,plan['root'],plan['bits'],plan['truth'],plan['output'],site,ground,allocate,priority,old[plan['root']].get('attributes',{}),input_loads if pin_policy=='load' else None)
    # Complete root sites are now known, including any collision relocation.
    destinations={p['output']:p['placed_site'] for p in plans}
    for request in encoders.values():
        sinks=[p['placed_site'] for p in plans if request['output'] in p['bits']]
        inputs=[points.get(drivers[b].rsplit('/',1)[0]) for b in request['bits']]
        def cost(s):return sum(abs(s[0]-p[0])+abs(s[1]-p[1]) for p in inputs if p)+output_weight*sum(abs(s[0]-p[0])+abs(s[1]-p[1]) for p in sinks)
        if not free:raise ValueError('No vacant type17 encoder site')
        site=min(free,key=lambda s:(cost(s),s));free.remove(site);request['site']=site
        added+=emit_function(new,placed,request['root'],request['bits'],request['truth'],request['output'],site,ground,allocate,priority,loads=input_loads if pin_policy=='load' else None)
    for plan in plans:
        if exact_truth(new,plan['root'],plan['cut_bits'])!=plan['source_truth']:raise ValueError('Physical function construction mismatch')
    dead=recover_dead_logic(new,placed)
    # Re-place encoder trees after DCE so they can use released upstream sites.
    live_encoders={r['root']:r for r in encoders.values() if r['root'] in cells}
    moving={}
    for name in live_encoders:
        spec=tree(new,name) if cells[name]['type'].startswith('MUX2_LUT') else None
        moving[name]=set(spec['leaves'])|set(spec['muxes']) if spec else {name}
    moving_cells=set().union(*moving.values()) if moving else set()
    occupied={(r['row'],r['column']) for category in ('clusters','mux_clusters','arithmetic_clusters') for n,r in placed.get(category,{}).items() if n not in moving_cells}
    occupied.update((r['row'],r['column']+i) for r in placed.get('hard_clusters',{}).values() for i in range(3))
    free={(r,c) for r in range(device.rows) for c in range(device.cols) if device.grid[r][c]==17 and (r,c) not in occupied}
    for name,request in live_encoders.items():
        sinks=[p['placed_site'] for p in plans if request['output'] in p['bits']];inputs=[points.get(drivers[b].rsplit('/',1)[0]) for b in request['bits']]
        def cost(s):return sum(abs(s[0]-p[0])+abs(s[1]-p[1]) for p in inputs if p)+output_weight*sum(abs(s[0]-p[0])+abs(s[1]-p[1]) for p in sinks)
        site=min(free,key=lambda s:(cost(s),s));free.remove(site);request['site']=site
        for n in moving[name]:(placed['clusters'].get(n) or placed['mux_clusters'][n]).update(row=site[0],column=site[1])
        for m in placed['wide_macros']:
            if m['root']==name:m.update(row=site[0],column=site[1])
    boundary=lambda cs:{n:c for n,c in cs.items() if not c['type'].startswith(('LUT','MUX2_LUT'))}
    if boundary(old)!=boundary(cells):raise ValueError('Changed state, hard resource or IO')
    fan=lambda cs:Counter(b for c in cs.values() for pin,bs in c['connections'].items() if c['port_directions'][pin]=='input' for b in bs)
    before,after=fan(old),fan(cells);changed_bits=sorted({b for r in rows for b in r['cut_bits']})
    placed.pop('weighted_internal_manhattan',None);placed.update(physical_legality_complete=False,timing_signoff=False)
    return new,placed,dict(operator='joint_multioutput_acd' if joint else 'shared_encoder_acd',roots=plans,encoders=list(encoders.values()),recovered_dead_cells=dead,joint_groups=joint_groups,
        removed_cells=removed,added_cells=added,register_delta=0,interface_cycles_changed=False,physical_lut8_used=False,
        lut_delta=sum(c['type'].startswith('LUT') for c in cells.values())-sum(c['type'].startswith('LUT') for c in old.values()),
        boundary_loads=[dict(bit=b,before=before[b],after=after[b],delta=after[b]-before[b]) for b in changed_bits],
        pin_policy=pin_policy,scope='Exact bounded ACD, normalized complementary encoder sharing; estimated priority and placement cost require actual routed validation.')
