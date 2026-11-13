"""Absorb upstream mapped logic into existing wide macro geometry.

Old upstream cells survive for other consumers. Global cuts prove the root
function, while every non-macro primitive and fixed physical boundary stays.
"""
import copy,hashlib,json,math,time
from collections import defaultdict
from ownpr_general_wide_recompile import info,apply,GeneralWideRecompileFeedback
from ownpr_multilevel_cut import Cuts
from ownpr_mux_macros import tree


class CrossMacroRecompileFeedback(GeneralWideRecompileFeedback):
    def comparison_boundary(self,design,root):
        return info(design,self.layout,root)

    def truth_function(self,design,root,boundary,members):
        return Cuts(design,k=7,depth=10).function(root,boundary)


def expanded_boundaries(design,root,model,boundary):
    cells=design['modules']['top']['cells'];private={b for c in cells.values() if c['type'].startswith('MUX2_LUT') for p in ('I0','I1') for b in c['connections'][p]}
    base=frozenset(boundary);found=set()
    for bit in boundary:
        name=model.drivers.get(bit)
        if name not in model.functions:continue
        if cells[name]['type'].startswith('MUX2_LUT'):
            _,inputs,_=info(design,{},name)
        else:inputs=model.functions[name][0]
        candidate=(base-{bit})|frozenset(inputs)
        if 1<=len(candidate)<=7 and not candidate&private:found.add(candidate)
    # Enumerated alternatives can jointly cross several old macro boundaries.
    output=model.functions[root][1]
    for candidate in model.enumerate(output):
        if candidate!=base and not candidate&private and output not in candidate:found.add(candidate)
    return sorted(found,key=lambda x:(len(x),tuple(sorted(x))))


def estimate(design,layout,spec,members,original,graphs,metrics,feedback,device):
    cells=design['modules']['top']['cells'];old=original['modules']['top']['cells'];at=metrics['setup']['arrival'];wires=defaultdict(list)
    drivers={b:n+'/'+p for n,c in cells.items() for p,bs in c['connections'].items() if c['port_directions'][p]=='output' for b in bs}
    for e in graphs['setup']['edges']:
        if e['kind']=='wire' and e['destination'].rsplit('/',1)[0] in members:
            name,pin=e['destination'].rsplit('/',1);bit=old[name]['connections'][pin][0];wires[bit].append(max(e['delay']))
    # Unmeasured cross-boundary wire lengths are not gain evidence. They are
    # ranking priors only; exact rerouting and full min/max determine acceptance.
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
    model=Cuts(design,k=7,depth=8,limit=96);eligible={};rejected=[];cells=design['modules']['top']['cells']
    drivers={bit:n+'/'+p for n,c in cells.items() for p,bs in c['connections'].items() if c['port_directions'][p]=='output' for bit in bs};at=metrics['setup']['arrival']
    for macro in layout.get('wide_macros',[]):
        try:eligible[macro['root']]=info(design,layout,macro['root'])
        except ValueError as e:rejected.append(dict(root=macro['root'],error=str(e)))
    roots,coverage=select_roots(design,layout,graphs['setup'],metrics['setup'],eligible,limit=40);queues={};diagnostics=[]
    for root in roots:
        if deadline is not None and time.monotonic()>deadline:break
        spec,base,members=eligible[root];candidates={}
        for boundary in expanded_boundaries(design,root,model,base)[:48]:
            if deadline is not None and time.monotonic()>deadline:break
            try:cut=model.function(root,boundary)
            except ValueError:continue
            if not (set(cut['interior'])-members) or cut['cut_width']==0:continue
            priority=sorted(cut['cut_bits'],key=lambda b:(-max(at.get(drivers.get(b),[-math.inf])),b))
            for bit in priority[:3]:
                try:
                    nd,nl,c=apply(design,layout,root,bit,priority,boundary)
                    score=max(at[root+'/O'])-estimate(nd,layout,spec,members,design,graphs,metrics,feedback,device)
                except (ValueError,KeyError):continue
                if not math.isfinite(score):continue
                c['old_wire_ranking_gain_ns']=score;c['expanded_interior']=cut['interior'];c['old_upstream_logic_retained']=True
                key=c['variant'];candidates[key]=(-score,nd,nl,c)
        picked=sorted(candidates.values(),key=lambda x:(x[0],x[3]['variant']))[:2]
        diagnostics.append(dict(root=root,candidates=len(candidates),best_ranking_gain_ns=-picked[0][0] if picked else None))
        for rank,nd,nl,c in picked:queues.setdefault(root,[]).append((min(metrics['setup']['slack'][root+'/O']),rank,root,nl,c,nd))
    rows=[]
    while any(queues.values()) and len(rows)<limit:
        for root in roots:
            if queues.get(root) and len(rows)<limit:rows.append(queues[root].pop(0))
    # Do not compose dependent expanded cuts without recertifying the combined
    # original-to-candidate graph. Individual transactions already cross macros.
    if audit is not None:audit.update(coverage,eligible_macros=len(eligible),macro_rejections=rejected,root_diagnostics=diagnostics,offered_candidates=len(rows),physical_candidates_measured=False)
    return rows
