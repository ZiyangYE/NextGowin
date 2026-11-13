"""Exact heterogeneous Shannon recompilation into existing private macro slots.

Branch-specific supports may differ. Global truth support need not equal the
physical mux level. This is fixed-geometry mapping feedback, not native signoff.
"""
import copy,hashlib,json,math,time
from collections import defaultdict
from functools import lru_cache
from ownpr_mux_macros import tree
from ownpr_multilevel_cut import Cuts
from ownpr_state_safety import protected_cells,active
from ownpr_existing_wide_remap import ExistingWideRemapFeedback,macro_truth
from register_control_map import normalize


@lru_cache(None)
def leaf_pin_order(slot):
    from ownpr_cell_timing import NativeCellTiming
    library=NativeCellTiming()
    return tuple(sorted(range(4),key=lambda i:(max(library.lut_input(i,slot,1)[2:4]),i)))


def info(design,layout,root):
    module=design['modules']['top'];cells=module['cells'];spec=tree(design,root);members=set(spec['leaves'])|set(spec['muxes'])
    if members&protected_cells(design):raise ValueError('Protected wide macro')
    observed={b for row in module.get('ports',{}).values() for b in row['bits']}
    observed.update(b for row in module.get('netnames',{}).values() if any(k.lower() in ('keep','syn_keep') and active(v) for k,v in row.get('attributes',{}).items()) for b in row['bits'])
    internal={b for n in members for p,bs in cells[n]['connections'].items() if cells[n]['port_directions'][p]=='output' for b in bs}
    if (internal-set(cells[root]['connections']['O']))&observed:raise ValueError('Observed internal macro value')
    local={n:c for n,c in cells.items() if n in members or c['type'] in ('GND','VCC')};model=Cuts(dict(modules={'top':dict(cells=local)}),k=7,depth=10)
    boundary=sorted({b for n in members for p,bs in cells[n]['connections'].items() if cells[n]['port_directions'][p]=='input' for b in bs if b not in internal and b not in model.constants})
    if not 1<=len(boundary)<=7:raise ValueError('Bounded recompile supports one to seven external variables')
    model.function(root,boundary)
    return spec,boundary,members


def cofactor(bits,word,select,value):
    index=bits.index(select);other=tuple(b for b in bits if b!=select);result=0
    for address in range(1<<len(other)):
        old=(address&((1<<index)-1))|((address>>index)<<(index+1))|(value<<index)
        result|=((word>>old)&1)<<address
    support,reduced=normalize(other,result)
    return tuple(support),reduced


def decompose(bits,word,level,priority,forced_root=None):
    """Find an exact fitting heterogeneous tree, or reject; no fit guarantee."""
    rank={b:i for i,b in enumerate(priority)}
    @lru_cache(None)
    def solve(bits,word,remaining,forced):
        if remaining==4:
            if len(bits)>4:return None
            return ('leaf',bits,word)
        candidates=[forced] if forced is not None else sorted(bits,key=lambda b:(rank.get(b,99),b))
        # Constants and functions of fewer than five variables still occupy
        # their existing hierarchy; duplicate cofactors without inventing nets.
        if not candidates:candidates=[priority[-1]]
        for bit in candidates:
            if bit in bits:
                left=cofactor(bits,word,bit,0);right=cofactor(bits,word,bit,1)
            else:left=right=(bits,word)
            a=solve(*left,remaining-1,None)
            if a is None:continue
            b=solve(*right,remaining-1,None)
            if b is not None:return ('mux',bit,a,b)
        return None
    if not priority:raise ValueError('No physical selector boundary')
    result=solve(tuple(bits),word,level,forced_root)
    if result is None:raise ValueError('Function cannot fit requested fixed macro tree')
    return result


def apply(design,layout,root,promoted_bit,priority=None,boundary_override=None):
    spec,boundary,members=info(design,layout,root)
    if boundary_override is None:before=macro_truth(design,root,boundary,members)
    else:
        boundary=sorted(boundary_override)
        before=Cuts(design,k=7,depth=10).function(root,boundary)
    support=before['cut_bits'];word=int(before['truth_hex'],16)
    if promoted_bit not in support:raise ValueError('Promoted bit is not in actual truth support')
    order=list(priority or sorted(support))
    if len(order)!=len(set(order)) or set(order)!=set(support):raise ValueError('Priority must cover actual support exactly')
    expression=decompose(tuple(support),word,spec['level'],order,promoted_bit)
    cells=design['modules']['top']['cells'];after=copy.deepcopy(design);new=after['modules']['top']['cells']
    zero=next((bs[0] for c in cells.values() if c['type']=='GND' for bs in c['connections'].values()),None)
    if zero is None:raise ValueError('Actual ground driver required for independent leaf pins')
    drivers={b:n for n in members for p,bs in cells[n]['connections'].items() if cells[n]['port_directions'][p]=='output' for b in bs}
    def emit(name,node):
        if node[0]=='leaf':
            bits,truth=node[1:];pin_order=sorted(bits,key=lambda b:(order.index(b),b))
            # Native I3 is much faster than I0, but use calibrated per-slot
            # values instead of baking this ordering into a generic mapper.
            # Fully constant leaves retain one timed dummy input, matching
            # the calibrated wide-mapping guard against unseeded LUT outputs.
            routed=pin_order or [order[-1]];pins=[zero]*4
            for pin,bit in zip(leaf_pin_order(layout['clusters'][name]['slot']),routed):pins[pin]=bit
            init=0
            for a in range(16):
                values={bit:(a>>i)&1 for i,bit in enumerate(pins) if bit!=zero}
                index=sum(values[bit]<<i for i,bit in enumerate(bits));init|=((truth>>index)&1)<<a
            for i,bit in enumerate(pins):new[name]['connections']['I'+str(i)]=[bit]
            new[name]['parameters']['INIT']=format(init,'016b');return
        new[name]['connections']['S0']=[node[1]]
        for pin,child in (('I0',node[2]),('I1',node[3])):emit(drivers[cells[name]['connections'][pin][0]],child)
    emit(root,expression)
    verified=macro_truth(after,root,boundary,members) if boundary_override is None else Cuts(after,k=7,depth=10).function(root,boundary)
    if (before['cut_bits'],before['truth_hex'])!=(verified['cut_bits'],verified['truth_hex']):raise ValueError('Exhaustive macro truth mismatch')
    if all(cells[n]==new[n] for n in members):raise ValueError('Identity recompilation')
    macro=next(m for m in layout['wide_macros'] if m['root']==root);tag=hashlib.sha256(json.dumps(expression).encode()).hexdigest()[:14]
    change=dict(operator='general_wide_recompile' if boundary_override is None else 'cross_macro_recompile',root=root,cells=sorted(members),destination=[macro['row'],macro['column']],neighbor_luts=sorted(spec['leaves']),variant=tag,
                promoted_bit=promoted_bit,priority=order,expression=expression,original_boundary=boundary,truth_support=support,truth_hex=before['truth_hex'],physical_positions_preserved=True,interface_cycles_changed=False)
    return after,copy.deepcopy(layout),change


class GeneralWideRecompileFeedback(ExistingWideRemapFeedback):
    boundary_info=staticmethod(info)


def estimate(design,layout,spec,members,original,graphs,metrics,feedback,device):
    cells=design['modules']['top']['cells'];old=original['modules']['top']['cells'];at=metrics['setup']['arrival'];wires=defaultdict(list)
    drivers={b:n+'/'+p for n,c in cells.items() for p,bs in c['connections'].items() if c['port_directions'][p]=='output' for b in bs}
    for e in graphs['setup']['edges']:
        if e['kind']=='wire' and e['destination'].rsplit('/',1)[0] in members:
            name,pin=e['destination'].rsplit('/',1);bit=old[name]['connections'][pin][0];wires[bit].append(max(e['delay']))
    default=max((max(v) for v in wires.values()),default=0.)
    def external(bit):return max(at[drivers[bit]])+max(wires.get(bit,[default]))
    cache={}
    def value(n):
        if n in cache:return cache[n]
        c=cells[n]
        if n in spec['leaves']:
            slot=layout['clusters'][n]['slot'];v=max((external(bs[0])+max(feedback.library.lut_input(int(p[1:]),slot,1)[2:4]) for p,bs in c['connections'].items() if p!='F' and bs[0] in drivers and drivers[bs[0]] in at),default=0.)
        else:
            row=layout['mux_clusters'][n];children={r['pin']:r['source'] for r in spec['links'] if r['destination']==n}
            def delay(p):return max(feedback.mux_library.delay(device.grid[row['row']][row['column']],row['level'],row['slot'],p,1)[2:4])
            v=max(value(children['I0'])+delay('I0'),value(children['I1'])+delay('I1'),external(c['connections']['S0'][0])+delay('S0'))
        cache[n]=v;return v
    return value(spec['root'])


def proposals(design,layout,device,metrics,graphs,feedback,limit=24,audit=None,deadline=None):
    from ownpr_global_timing_frontier import select_roots
    cells=design['modules']['top']['cells'];drivers={b:n+'/'+p for n,c in cells.items() for p,bs in c['connections'].items() if c['port_directions'][p]=='output' for b in bs};at=metrics['setup']['arrival']
    details={};rejected=[]
    for macro in layout.get('wide_macros',[]):
        try:details[macro['root']]=info(design,layout,macro['root'])
        except ValueError as e:rejected.append(dict(root=macro['root'],error=str(e)))
    roots,coverage=select_roots(design,layout,graphs['setup'],metrics['setup'],details,limit=40);rows=[];best_by_root={}
    for root in roots:
        if deadline is not None and time.monotonic()>deadline:break
        spec,boundary,members=details[root];cut=macro_truth(design,root,boundary,members)
        wire_by_bit=defaultdict(list)
        for e in graphs['setup']['edges']:
            if e['kind']=='wire' and e['destination'].rsplit('/',1)[0] in members:
                n,p=e['destination'].rsplit('/',1);wire_by_bit[cells[n]['connections'][p][0]].append(max(e['delay']))
        priority=sorted(cut['cut_bits'],key=lambda b:(-max(at.get(drivers.get(b),[-math.inf]))-max(wire_by_bit.get(b,[0.])),b))
        candidates=[]
        for bit in priority:
            try:nd,nl,c=apply(design,layout,root,bit,priority)
            except ValueError:continue
            score=max(at[root+'/O'])-estimate(nd,layout,spec,members,design,graphs,metrics,feedback,device)
            if not math.isfinite(score):continue
            c['old_wire_ranking_gain_ns']=score
            candidates.append((min(metrics['setup']['slack'][root+'/O']),-score,root,nl,c,nd))
        candidates.sort(key=lambda x:(x[1],x[4]['variant']))
        if candidates:best_by_root[root]=candidates[0];rows.extend(candidates[:2])
    batches=[]
    for maximum in (12,6,3):
        nd=design;individuals=[]
        for root,row in best_by_root.items():
            c=row[4]
            try:nd,_,updated=apply(nd,layout,root,c['promoted_bit'],c['priority'])
            except ValueError:continue
            individuals.append(updated)
            if len(individuals)>=maximum:break
        if len(individuals)<2:continue
        tag=hashlib.sha256(json.dumps([(c['root'],c['expression']) for c in individuals]).encode()).hexdigest()[:14]
        c=dict(operator='general_wide_recompile_batch',cells=sorted(set().union(*(set(c['cells']) for c in individuals))),destination=[0,0],neighbor_luts=[c['root'] for c in individuals],variant=tag,
               individuals=individuals,interface_cycles_changed=False,physical_positions_preserved=True)
        batches.append((metrics['setup']['wns'],0.,'__general_cofactor_batch_'+str(maximum),copy.deepcopy(layout),c,nd))
    if audit is not None:audit.update(coverage,eligible_macros=len(details),macro_rejections=rejected,offered_candidates=len(rows)+len(batches),atomic_batches=len(batches),physical_candidates_measured=False)
    return (batches+rows)[:limit]
