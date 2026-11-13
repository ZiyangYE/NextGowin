"""Measured remapping inside existing private wide macros, with fixed state.

Reuses the canonical Shannon transformation, preserves all physical slots and
output identities, and verifies the whole macro truth table. Ranking is only
an old-wire estimate. Loaded rerouting, complete min/max propagation, CEC and
native export must decide acceptance. No project-specific rule is used.
"""
import copy,hashlib,itertools,json,math,time
from collections import defaultdict
from ownpr_mux_macros import tree,validate as validate_mux
from ownpr_multilevel_cut import Cuts
from ownpr_state_safety import protected_cells,active
from reorder_ownpr_wide_inputs import reorder
from ownpr_wide_mapping_feedback import WideMappingFeedback,COMB,LUTS


def info(design,layout,root):
    cells=design['modules']['top']['cells'];spec=tree(design,root)
    members=set(spec['leaves'])|set(spec['muxes'])
    if members&protected_cells(design):raise ValueError('Protected wide macro')
    kept={b for row in design['modules']['top'].get('netnames',{}).values() if any(k.lower() in ('keep','syn_keep') and active(v) for k,v in row.get('attributes',{}).items()) for b in row['bits']}
    kept.update(b for row in design['modules']['top'].get('ports',{}).values() for b in row['bits'])
    if any(b in kept for n in members-{root} for p,bs in cells[n]['connections'].items() if cells[n]['port_directions'][p]=='output' for b in bs):raise ValueError('Observed internal macro value')
    constants={'0','1'}
    for c in cells.values():
        if c['type'] in ('GND','VCC'):constants.update(next(iter(c['connections'].values())))
    bit=next((b for n in spec['leaves'] for i in range(4) for b in cells[n]['connections']['I'+str(i)] if b not in constants),None)
    if bit is None:raise ValueError('Constant wide macro')
    _,change=reorder(design,root,bit)
    return spec,change['old_order'],members


def macro_truth(design,root,boundary,members):
    cells=design['modules']['top']['cells']
    local={n:c for n,c in cells.items() if n in members or c['type'] in ('GND','VCC')}
    return Cuts(dict(modules={'top':dict(cells=local)}),k=7,depth=10).function(root,boundary)


def apply(design,layout,root,steps):
    spec,boundary,members=info(design,layout,root);current=design;applied=[]
    before=macro_truth(design,root,boundary,members)
    for bit,target in steps:
        _,order,_=info(current,layout,root)
        if order[target]==bit:continue
        current,_=reorder(current,root,bit,target);applied.append([bit,target])
    if not applied:raise ValueError('Identity wide reorder')
    after=macro_truth(current,root,boundary,members)
    if (before['cut_bits'],before['truth_hex'])!=(after['cut_bits'],after['truth_hex']):raise ValueError('Wide macro truth changed')
    _,order,_=info(current,layout,root)
    macro=next(m for m in layout['wide_macros'] if m['root']==root)
    tag=hashlib.sha256(json.dumps([root,order]).encode()).hexdigest()[:14]
    change=dict(operator='existing_wide_remap',root=root,cells=sorted(members),destination=[macro['row'],macro['column']],
        neighbor_luts=sorted(spec['leaves']),variant=tag,reordering_steps=applied,old_order=boundary,new_order=order,
        truth_hex=before['truth_hex'],truth_support=before['cut_bits'],physical_positions_preserved=True,interface_cycles_changed=False)
    return current,copy.deepcopy(layout),change


class ExistingWideRemapFeedback(WideMappingFeedback):
    boundary_info=staticmethod(info)

    def comparison_boundary(self,design,root):
        return self.boundary_info(self.reference,self.layout,root)

    def truth_function(self,design,root,boundary,members):
        return macro_truth(design,root,boundary,members)

    def admit(self,design,layout,static):
        old=self.reference['modules']['top'];new=design['modules']['top']
        if {k:v for k,v in self.reference.items() if k!='modules'}!={k:v for k,v in design.items() if k!='modules'} or set(self.reference['modules'])!=set(design['modules']):raise ValueError('Changed design contract')
        if any(self.reference['modules'][n]!=design['modules'][n] for n in design['modules'] if n!='top'):raise ValueError('Changed auxiliary module')
        if {k:v for k,v in old.items() if k!='cells'}!={k:v for k,v in new.items() if k!='cells'} or layout!=self.layout:raise ValueError('Changed interface, metadata or physical geometry')
        a,b=old['cells'],new['cells']
        if set(a)!=set(b):raise ValueError('Changed primitive membership')
        changed={n for n in a if a[n]!=b[n]};covered=set()
        for macro in layout.get('wide_macros',[]):
            root=macro['root'];spec=tree(self.reference,root);members=set(spec['leaves'])|set(spec['muxes'])
            if not members&changed:continue
            _,boundary,_=self.comparison_boundary(design,root);allowed=set(boundary)
            for n in members:
                x,y=a[n],b[n]
                if set(x['connections'])!=set(y['connections']) or any(len(bs)!=1 for bs in y['connections'].values()):raise ValueError('Changed primitive pin shape')
                if {k:v for k,v in x.items() if k not in ('parameters','connections')}!={k:v for k,v in y.items() if k not in ('parameters','connections')}:raise ValueError('Changed primitive metadata')
                if x['type'] in LUTS:
                    if y['type']!='LUT4' or set(y['parameters'])!={'INIT'} or len(y['parameters']['INIT'])!=16 or set(y['parameters']['INIT'])-{'0','1'} or x['connections']['F']!=y['connections']['F']:raise ValueError('Changed LUT contract/output')
                    # Repruning can bind a now-independent input to the same
                    # design's actual constant driver; CEC still checks truth.
                    constants={'0','1'}
                    for cell in b.values():
                        if cell['type'] in ('GND','VCC'):constants.update(next(iter(cell['connections'].values())))
                    if any(bit not in allowed|constants for p,bs in y['connections'].items() if p!='F' for bit in bs):raise ValueError('New external wide boundary')
                else:
                    if x['parameters']!=y['parameters'] or any(x['connections'][p]!=y['connections'][p] for p in ('I0','I1','O')) or y['connections']['S0'][0] not in allowed:raise ValueError('Changed dedicated topology or boundary')
            x=self.truth_function(self.reference,root,boundary,members);y=self.truth_function(design,root,boundary,members)
            if (x['cut_bits'],x['truth_hex'])!=(y['cut_bits'],y['truth_hex']):raise ValueError('Changed wide function')
            covered.update(members)
        if changed-covered:raise ValueError('Changed state, hard cells or non-admitted logic')
        stable=lambda rows:[r for r in rows if r.get('kind') not in ('mux_dedicated_link','lut_truth_independent_constant_input')]
        if stable(static)!=stable(self.static):raise ValueError('Changed fixed static configuration')
        checked=validate_mux(design,layout,self.device)
        expected={(r['source'],r['destination'],r['pin'],r['row'],r['column'],r['source_slot'],r['destination_slot']) for r in checked['links']}
        actual={(r['source'],r['destination'],r['pin'],r['row'],r['column'],r['source_slot'],r['destination_slot']) for r in static if r.get('kind')=='mux_dedicated_link'}
        if expected!=actual:raise ValueError('Changed dedicated links')
        return checked


def proposals(design,layout,device,metrics,graphs,feedback,limit=24,audit=None,deadline=None):
    cells=design['modules']['top']['cells'];eligible=[];rejected=[];details={}
    for macro in layout.get('wide_macros',[]):
        root=macro['root']
        try:details[root]=info(design,layout,root);eligible.append(root)
        except ValueError as error:rejected.append(dict(root=root,error=str(error)))
    from ownpr_global_timing_frontier import select_roots
    roots,coverage=select_roots(design,layout,graphs['setup'],metrics['setup'],eligible,limit=40)
    drivers={bit:n+'/'+p for n,c in cells.items() for p,bs in c['connections'].items() if c['port_directions'][p]=='output' for bit in bs}
    at=metrics['setup']['arrival'];slacks=metrics['setup']['slack'];queues={}
    def finish(d,spec,members):
        local=d['modules']['top']['cells'];wires=defaultdict(list)
        for e in graphs['setup']['edges']:
            name,pin=e['destination'].rsplit('/',1)
            if e['kind']=='wire' and name in members and e['source'].rsplit('/',1)[0] not in members:
                bit=cells[name]['connections'][pin][0];wires[bit].append(max(e['delay']))
        def external(bit):return max(at[drivers[bit]])+max(wires.get(bit,[0.]))
        cache={}
        def value(n):
            if n in cache:return cache[n]
            c=local[n]
            if n in spec['leaves']:
                slot=layout['clusters'][n]['slot'];v=max((external(bs[0])+max(feedback.library.lut_input(int(p[1:]),slot,1)[2:4]) for p,bs in c['connections'].items() if p!='F' and bs[0] in drivers and drivers[bs[0]] in at),default=0.)
            else:
                row=layout['mux_clusters'][n];children={r['pin']:r['source'] for r in spec['links'] if r['destination']==n}
                delay=lambda p:max(feedback.mux_library.delay(device.grid[row['row']][row['column']],row['level'],row['slot'],p,1)[2:4])
                v=max(value(children['I0'])+delay('I0'),value(children['I1'])+delay('I1'),external(c['connections']['S0'][0])+delay('S0'))
            cache[n]=v;return v
        return value(spec['root'])
    for root in roots:
        if deadline is not None and time.monotonic()>deadline:break
        spec,order,members=details[root];baseline=finish(design,spec,members);candidates={}
        steps=[[(bit,target)] for target in range(4,len(order)) for bit in order if bit!=order[target]]
        if len(order)==6:
            latest=sorted(order,key=lambda b:(-max(at[drivers[b]]),b))[:3]
            steps += [[(a,4),(b,5)] for a,b in itertools.permutations(latest,2)]
        for sequence in steps:
            if deadline is not None and time.monotonic()>deadline:break
            try:nd,nl,change=apply(design,layout,root,sequence);score=baseline-finish(nd,spec,members)
            except (ValueError,KeyError):continue
            if not math.isfinite(score) or score<=.01:continue
            change['old_wire_only_ranking_gain_ns']=score
            key=tuple(change['new_order']);candidates[key]=(-score,nd,nl,change)
        for score,nd,nl,change in sorted(candidates.values(),key=lambda r:(r[0],r[3]['variant']))[:2]:
            queues.setdefault(root,[]).append((min(slacks[root+'/O']),score,root,nl,change,nd))
    result=[]
    while any(queues.values()) and len(result)<limit:
        for root in roots:
            if queues.get(root) and len(result)<limit:result.append(queues[root].pop(0))
    # Whole-design atomic proposals combine distinct macro functions. Shared
    # source nets still interact physically, so all trees and both timing
    # graphs must be measured again; individual gains are never added up.
    batches=[]
    for maximum in (12,6,3):
        nd=design;individuals=[];seen=set()
        for row in result:
            change=row[4];root=change['root']
            if root in seen:continue
            try:nd,_,updated=apply(nd,layout,root,change['reordering_steps'])
            except ValueError:continue
            seen.add(root);individuals.append(updated)
            if len(individuals)>=maximum:break
        if len(individuals)<2:continue
        tag=hashlib.sha256(json.dumps([(c['root'],c['new_order']) for c in individuals]).encode()).hexdigest()[:14]
        change=dict(operator='existing_wide_remap_batch',cells=sorted(set().union(*(set(c['cells']) for c in individuals))),destination=[0,0],
            neighbor_luts=sorted(seen),variant=tag,individuals=individuals,interface_cycles_changed=False,physical_positions_preserved=True)
        batches.append((metrics['setup']['wns'],0.,'__existing_wide_batch_'+str(maximum),copy.deepcopy(layout),change,nd))
    result=batches+result
    if audit is not None:audit.update(coverage,canonical_macros=len(eligible),macro_rejections=rejected,offered_candidates=len(result),atomic_batches=len(batches),physical_candidates_measured=False)
    return result
