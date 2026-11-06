"""Joint path-covered, multilevel Boolean cuts with calibrated physical macros."""
import argparse,json,sys,time,shutil
from pathlib import Path
from ownpr_device import ROOT,Device
from ownpr_image_storage import load_json,sha
from ownpr_multilevel_cut import Cuts,replace,wide_absorption_boundaries
from ownpr_critical_chain_move import exclusive_tiles,locations,apply as validate
from ownpr_timing_wide_remap import place

def candidates(design,layout,ranking,window=2.,min_cells=3,cut_width=6):
    model=Cuts(design,k=cut_width);cells=model.cells;excluded=exclusive_tiles(layout);traces=ranking['traces']
    private={b for c in cells.values() if c['type'].startswith('MUX2_LUT') for pin in ('I0','I1') for b in c['connections'][pin]}
    if not traces:raise ValueError('No actual timing path witnesses')
    worst=min(p['slack_ns'] for p in traces);target={i for i,p in enumerate(traces) if p['slack_ns']<=worst+window};roots=set()
    for index in target:
        for arc in traces[index]['ordinary_wires']:
            name=arc['endpoint'].rsplit('/',1)[0];site=layout['clusters'].get(name)
            if site and cells[name]['type'] in ('LUT1','LUT2','LUT3','LUT4') and (site['row'],site['column']) not in excluded:roots.add(name)
    choices=[];seen=set()
    for root in sorted(roots):
        boundaries=list(wide_absorption_boundaries(design,model,root)) if cut_width==7 else []
        boundaries+=list(model.enumerate(model.functions[root][1]))
        for boundary in boundaries:
            if not boundary or model.functions[root][1] in boundary:continue
            cut=model.function(root,boundary)
            if cut['interior_count']<min_cells or not 1<=cut['cut_width']<=cut_width:continue
            if cut['cut_width']==7 and layout['clusters'][root].get('ff'):continue  # No combined LUT7/FF calibration.
            if set(cut['cut_bits'])&private:continue
            key=(root,tuple(cut['cut_bits']),cut['truth_hex'])
            if key in seen:continue
            seen.add(key);inside=set(cut['interior']);witness=[];gain={};late={}
            for index in sorted(target):
                arcs=traces[index]['ordinary_wires'];end=next((i for i,a in enumerate(arcs) if a['endpoint'].rsplit('/',1)[0]==root),None)
                if end is None:continue
                prefix=arcs[:end+1];nodes={a['endpoint'].rsplit('/',1)[0] for a in prefix}&inside
                eliminated=[a for a in prefix if a['endpoint'].rsplit('/',1)[0] in inside and model.drivers.get(int(a['net'].split('_')[-1])) in inside]
                if len(nodes)<2 or not eliminated:continue
                incoming=[int(a['net'].split('_')[-1]) for a in prefix if int(a['net'].split('_')[-1]) in cut['cut_bits']]
                # This is only a priority proxy. New routes and MUX input delays
                # are not subtracted because those must be measured after P&R.
                gain[index]=sum(a['wire_delay_ns'] for a in eliminated)+.35*(len(nodes)-1)
                late[index]=incoming[-1] if incoming else None;witness.append(index)
            if not witness:continue
            critical=min(witness,key=lambda i:(traces[i]['slack_ns'],-gain[i],i))
            choices.append(dict(cut=cut,witness_paths=witness,priority_proxy_ns=gain,late_bit=late[critical],
                worst_slack_ns=traces[critical]['slack_ns'],lut_growth=max(0,(1<<max(0,cut['cut_width']-4))-1),
                paired_ff=layout['clusters'][root].get('ff')))
    return choices,target

def select(options,target,count=8,max_growth=24,required_gain=None):
    if not 1<=count<=16 or not 0<=max_growth<=48:raise ValueError('Invalid cut batch limits')
    pending=set(target);selected=[];used=set();growth=0
    demand={i:float(required_gain[i]) for i in target} if required_gain is not None else None
    if demand is not None and any(not 0<v<=20 for v in demand.values()):raise ValueError('Invalid per-path gain demand')
    remaining=dict(demand) if demand is not None else None;accumulated={i:0. for i in target}
    while pending and len(selected)<count:
        eligible=[]
        for row in options:
            inside=set(row['cut']['interior']);covered=set(row['witness_paths'])&pending
            if not covered or inside&used or growth+row['lut_growth']>max_growth:continue
            if demand is None:
                score=(row['worst_slack_ns'],-len(covered),-sum(row['priority_proxy_ns'][i] for i in covered),row['lut_growth'],row['cut']['root'])
            else:
                # Equalize estimated residual deficits, allowing several
                # disjoint cuts along one critical path. This remains a proxy.
                reduction=sum(min(remaining[i],row['priority_proxy_ns'][i]) for i in covered)
                score=(-max(remaining[i] for i in covered),-reduction,row['lut_growth'],row['cut']['root'])
            eligible.append((score,row,covered))
        if not eligible:break
        _,row,covered=min(eligible,key=lambda x:x[0]);selected.append(dict(row,newly_covered_paths=sorted(covered)))
        used.update(row['cut']['interior']);growth+=row['lut_growth']
        for i in covered:accumulated[i]+=row['priority_proxy_ns'][i]
        if remaining is None:pending-=covered
        else:
            for i in covered:remaining[i]=max(0.,remaining[i]-row['priority_proxy_ns'][i])
            pending={i for i in pending if remaining[i]>1e-9}
    touched={i for i,v in accumulated.items() if v>0}
    return selected,dict(target_paths=sorted(target),covered_paths=sorted(set(target)-pending),uncovered_paths=sorted(pending),lut_growth=growth,
        touched_paths=sorted(touched),required_gain_ns=demand,accumulated_priority_proxy_ns=accumulated,remaining_gain_proxy_ns=remaining,
        coverage_definition='Proxy gain demand satisfied, not routed closure' if demand is not None else 'At least one structural cut, not routed closure')

def physical_cost(design,layout,cut,late):
    points=locations(layout);cells=design['modules']['top']['cells'];drivers={b:n for n,c in cells.items() for p,bs in c['connections'].items() if c['port_directions'][p]=='output' for b in bs}
    root=cut['root'];out=cells[root]['connections']['F'][0];inputs=[(points[drivers[b]],6. if b==late else 1.) for b in cut['cut_bits'] if drivers.get(b) in points]
    sinks=[points[n] for n,c in cells.items() if n in points for pin,bs in c['connections'].items() if c['port_directions'][pin]=='input' and out in bs]
    origin=points[root]
    def cost(r,c):
        dist=lambda p:abs(r-p[0])+abs(c-p[1])
        return sum(w*dist(p) for p,w in inputs)+4*sum(dist(p) for p in sinks)/max(1,len(sinks))+.1*dist(origin)
    return cost

def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('base','routes','sta','out'):p.add_argument('--'+name,type=Path,required=True)
    p.add_argument('--count',type=int,default=8);p.add_argument('--window',type=float,default=2.);p.add_argument('--lut-growth',type=int,default=24);p.add_argument('--plan-only',action='store_true');p.add_argument('--legalize-macros',action='store_true');p.add_argument('--gain-target-ns',type=float,default=0.)
    p.add_argument('--pin-feedback',type=Path);p.add_argument('--protect-slack',type=float,default=.5);p.add_argument('--protect-target-mhz',type=float)
    p.add_argument('--cut-width',type=int,choices=(6,7),default=6);p.add_argument('--min-cut-cells',type=int,default=3);p.add_argument('--share-wide-luts',action='store_true')
    p.add_argument('--input-register-copies',type=int,default=0);p.add_argument('--input-copy-min-span',type=int,default=8)
    p.add_argument('--pin-frontier',action='store_true');p.add_argument('--wire',type=Path);p.add_argument('--pin-root-limit',type=int,default=256);a=p.parse_args()
    if not 1<=a.count<=16 or not 0<a.window<=5 or not 0<=a.lut_growth<=48:p.error('Bounded proposal limits exceeded')
    if not 0<=a.gain_target_ns<=5:p.error('Gain target must be 0..5 ns')
    if a.gain_target_ns>a.window:p.error('Path window must cover the requested gain target')
    if not 2<=a.min_cut_cells<=8:p.error('Minimum cut interior must be 2..8 cells')
    if a.protect_target_mhz is not None and not a.pin_feedback:p.error('Target protection requires checked pin feedback')
    if a.share_wide_luts and not a.legalize_macros:p.error('Shared wide LUTs require pattern legalization')
    if not 0<=a.input_register_copies<=8 or not 1<=a.input_copy_min_span<=100:p.error('Input-copy limits exceeded')
    if a.input_register_copies and (not a.pin_feedback or a.protect_target_mhz is None):p.error('Joint input copies require checked feedback and target-period protection')
    if a.pin_frontier and (not a.pin_feedback or not a.wire or not 1<=a.pin_root_limit<=1024):p.error('Pin-frontier mapping requires checked pin/wire feedback and bounded roots')
    from workspace_budget import reserve
    reserve(ROOT,100_000_000);started=time.monotonic()
    from ownpr_critical_tree_rebuild import verify_feedback
    verify_feedback(a.base,a.routes,a.sta)
    sys.path.insert(0,str(ROOT/'experiments'));from analyze_ownpr_critical_wires import analyze
    ranking=analyze(a.sta,a.base,a.routes,512);design=load_json(a.base/'packed.json');layout=load_json(a.base/'placement.json')
    device=Device();pin_context=None;discovery=None
    if a.pin_frontier:
        from ownpr_pin_feedback import load
        from ownpr_pin_cut_selection import measured_inputs,discover,select_roots
        budgets,info=load(a.pin_feedback,a.sta);pin_context=(budgets,info);measured,wire_info=measured_inputs(a.routes,a.wire,a.base)
        reported={arc['endpoint'].rsplit('/',1)[0] for path in ranking['traces'] for arc in path['ordinary_wires']}
        options,discovery=discover(design,layout,budgets,measured,device,a.window,a.cut_width,a.min_cut_cells,a.pin_root_limit,reported)
        discovery['wire_source']=wire_info;chosen,coverage=select_roots(options,a.count,a.lut_growth)
    else:
        options,target=candidates(design,layout,ranking,a.window,a.min_cut_cells,a.cut_width)
        required=None
        if a.gain_target_ns:
            worst=min(p['slack_ns'] for p in ranking['traces'])
            required={i:max(0.,a.gain_target_ns-(ranking['traces'][i]['slack_ns']-worst)) for i in target}
            target={i for i,v in required.items() if v>1e-9};required={i:required[i] for i in target}
        chosen,coverage=select(options,target,a.count,a.lut_growth,required)
    a.out.mkdir(parents=True,exist_ok=False)
    (a.out/'plan.json').write_text(json.dumps(dict(chosen=chosen,coverage=coverage,options=options,pin_discovery=discovery),indent=2))
    if a.plan_only:
        print(json.dumps(dict(chosen=[dict(root=r['cut']['root'],width=r['cut']['cut_width'],cells=r['cut']['interior_count'],paths=len(r['witness_paths']),slack=r['worst_slack_ns']) for r in chosen],coverage=coverage),indent=2));return
    if not chosen:raise ValueError('No multilevel candidate covers the critical window')
    protected_names=set();protection=None
    if a.pin_feedback:
        from ownpr_pin_feedback import load
        from ownpr_feedback_transport import protected_placement_cells
        budgets,info=pin_context if pin_context is not None else load(a.pin_feedback,a.sta);window=a.protect_slack;conversion=None
        if a.protect_target_mhz is not None:
            from ownpr_target_protection import from_capture
            window,conversion=from_capture(a.sta,a.protect_target_mhz,a.protect_slack)
        protected_names,protection=protected_placement_cells(design,layout,budgets,window)
        protection['target_conversion']=conversion
        protection['capture']=info;(a.out/'placement_protection.json').write_text(json.dumps(protection,indent=2))
    after=design;placed=layout;changes=[]
    for row in chosen:
        cost=physical_cost(after,placed,row['cut'],row['late_bit']);legalization=None
        share=a.share_wide_luts and row['cut']['cut_width'] in (5,6) and not placed['clusters'][row['cut']['root']].get('ff')
        if a.legalize_macros and row['cut']['cut_width']>4:
            from ownpr_macro_legalize import clear_site
            model=Cuts(after);root=row['cut']['root'];output=model.functions[root][1]
            protected={model.drivers.get(b) for b in row['cut']['cut_bits']}|protected_names
            protected.update(n for n,c in model.cells.items() for pin,bs in c['connections'].items() if c['port_directions'][pin]=='input' and output in bs)
            future_roots={r['cut']['root'] for r in chosen[len(changes)+1:]} if a.pin_frontier else set()
            placed,target,legalization=clear_site(after,placed,root,device,cost,protected,wide_leaf_slots=tuple(range(1<<(row['cut']['cut_width']-4))) if share else None,reserved_roots=future_roots)
            cost=lambda r,c:0. if (r,c)==target else float('inf')
        after,logical=replace(after,row['cut'],row['late_bit'])
        if row['cut']['cut_width']>4:
            placed,physical=place(after,placed,row['cut']['root'],device,allow_paired=True,site_cost=cost,share_ordinary=share)
            if legalization:
                physical.update(legalization=legalization,fixed_other_cells=not bool(legalization['moves']),clock_replan_required=physical['clock_replan_required'] or legalization['clock_replan_required'])
        else:physical=dict(placement_unchanged=True,clock_replan_required=False)
        changes.append(dict(logical=logical,physical=physical))
    frontier=None;aliases={}
    if a.input_register_copies:
        from ownpr_cut_state_frontier import apply as copy_inputs
        after,placed,frontier=copy_inputs(design,after,placed,changes,budgets,device,conversion['period_reduction_ns'],a.input_register_copies,a.input_copy_min_span,protected=protected_names)
        (a.out/'input_frontier_plan.json').write_text(json.dumps(frontier,indent=2));aliases=frontier['register_replicas']
        if not aliases:raise ValueError('No supported input register copy has target-period headroom')
    validate(after,placed,device,dict(sites={}))
    if aliases:
        from ownpr_register_equivalence import prove_replicas
        proof=prove_replicas(design,after,aliases,a.out/'cec',180)
    else:
        from native_boundary_cec import prove
        proof=prove(design,after,a.out/'cec',180)
        if not proof or not all(r['cec']['equivalent'] for r in proof):raise ValueError('Joint cut proof incomplete')
    for name,value in [('packed.json',after),('placement.json',placed),('changes.json',changes)]:(a.out/name).write_text(json.dumps(value,indent=2))
    sources=[Path(__file__),ROOT/'scripts/ownpr_multilevel_cut.py',ROOT/'scripts/ownpr_timing_wide_remap.py',ROOT/'scripts/ownpr_macro_legalize.py',ROOT/'scripts/ownpr_feedback_transport.py',ROOT/'scripts/ownpr_pin_feedback.py',ROOT/'scripts/ownpr_target_protection.py',ROOT/'scripts/ownpr_wide_lut_sharing.py',ROOT/'scripts/ownpr_mux_macros.py',ROOT/'scripts/ownpr_mux_configuration.py',ROOT/'scripts/ownpr_cut_state_frontier.py',ROOT/'scripts/ownpr_regional_replication.py',ROOT/'scripts/ownpr_register_equivalence.py']
    if a.pin_frontier:sources.extend([ROOT/'scripts/ownpr_pin_cut_selection.py',ROOT/'scripts/ownpr_cell_timing.py',ROOT/'scripts/ownpr_mux_architecture.py'])
    for src in sources:shutil.copyfile(src,a.out/(src.name+'.txt'))
    result=dict(status='placed',netlist_sha256=sha(a.out/'packed.json'),placement_sha256=sha(a.out/'placement.json'),source_netlist_sha256=sha(a.base/'packed.json'),source_placement_sha256=sha(a.base/'placement.json'),source_sta_sha256=sha(a.sta/'probe.tr'),
        proof=proof,coverage=coverage,changes=changes,placement_protection=protection,cut_width_limit=a.cut_width,minimum_cut_cells=a.min_cut_cells,seven_input_ff_sharing_supported=False,source_sha256={src.name:sha(a.out/(src.name+'.txt')) for src in sources},
        synthesis_pnr_cooptimization=True,mapping_selection='pin_frontier' if a.pin_frontier else 'reported_paths',pin_discovery=discovery,input_frontier=frontier,clock_replan_required=bool(aliases) or any(c['physical']['clock_replan_required'] for c in changes),interface_cycles_changed=False,state_cells_unchanged=not bool(aliases),vendor_pnr_executed=False,timing_signoff=False,wall_seconds=time.monotonic()-started)
    (a.out/'manifest.json').write_text(json.dumps(result,indent=2));print(json.dumps(dict(changes=changes,coverage=coverage),indent=2))

if __name__=='__main__':main()
