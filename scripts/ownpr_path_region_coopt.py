"""Path-grouped wide-macro and state-frontier replication near data sinks.

The ranking uses measured old path wires as a potential, not a new delay
prediction. All original hard/state boundaries remain; new state needs
inductive equivalence and changed loading needs complete physical signoff.
"""
import argparse,json,sys,time,shutil
from collections import defaultdict
from pathlib import Path
from ownpr_device import ROOT,Device
from ownpr_image_storage import load_json,read_text,sha
from ownpr_critical_chain_move import locations
from routing_path_diagnosis import diagnose
from ownpr_regional_replication import replicate as region,LUTS
from ownpr_macro_replication import replicate as wide


def propose(design,layout,device,paths,reverse,period,target,groups=5,depth=4,coverage=False,legalize=False,launch_frontier=False,cluster_regions=False,allow_state_roots=False,protected_cells=()):
    cells=design['modules']['top']['cells'];points=locations(layout)
    selected=[];rejections=[];aliases={};used_sources=set();used_sinks=set()
    deficit=lambda p:max(0.,period-1000/target-p['slack_ns'])
    by_launch=defaultdict(list)
    for p in paths:
        if deficit(p)>0:by_launch[p['source']].append(p)
    launches=sorted(by_launch,key=lambda n:(-sum(deficit(p) for p in by_launch[n]),min(p['slack_ns'] for p in by_launch[n]))) if coverage else sorted(by_launch,key=lambda n:min(p['slack_ns'] for p in by_launch[n]))
    changed,placed=design,layout
    for launch in launches:
        if len(selected)>=groups:break
        witnesses=by_launch[launch];edges=defaultdict(dict)
        for p in witnesses:
            for edge in p['edges']:
                src=reverse.get(edge['source'].rsplit('/',1)[0]);dst,pin=edge['destination'].rsplit('/',1);dst=reverse.get(dst)
                if src not in cells or dst not in cells or src in used_sources or (dst,pin) in used_sinks:continue
                if cells[src]['type'] not in LUTS|{'MUX2_LUT5','MUX2_LUT6','MUX2_LUT7'}|({'DFFRE','DFFSE'} if allow_state_roots else set()):continue
                typ=cells[dst]['type']
                if not (typ in LUTS and pin in ('I0','I1','I2','I3') or typ=='ALU' and pin in ('I0','I1','I3') or typ.startswith('MUX2_LUT') and pin=='S0'):continue
                edges[src][dst,pin]=max(edges[src].get((dst,pin),0.),edge['delay_ns'])
        # Evaluate bounded roots and spatial receiver groups; source identities
        # come exclusively from the checked report, never benchmark names.
        roots=sorted(edges,key=lambda n:(-sum(edges[n].values()),n))[:16];options=[]
        for src in roots:
            for anchor in sorted(edges[src],key=lambda s:-edges[src][s])[:3]:
                center=points[anchor[0]]
                sinks=sorted(s for s in edges[src] if abs(points[s[0]][0]-center[0])+abs(points[s[0]][1]-center[1])<=6)
                if any(s in used_sinks for s in sinks):continue
                try:
                    is_wide=cells[src]['type'].startswith('MUX2_LUT')
                    if is_wide:nd,np,change=wide(changed,placed,device,src,sinks,legalize=legalize,protected_cells=protected_cells)
                    else:nd,np,change=region(changed,placed,device,src,sinks,depth,max_registers=12,allowed_registers={reverse[launch]} if launch_frontier else None,
                        cluster_region=cluster_regions and cells[src]['type'] in ('DFFRE','DFFSE'),protected_cells=protected_cells)
                    if cluster_regions and not is_wide and (change['lut_growth']>8 or change['register_growth']>3):raise ValueError('Regional cone exceeds one-CFU packing capacity')
                except ValueError as e:
                    rejections.append(dict(launch=launch,source=src,sinks=sinks,error=str(e)));continue
                copied=set(change.get('cone',[]))|set(change.get('register_replicas',{}).values())|set(change.get('members',{}))|{src}
                relief={}
                for p in witnesses:
                    parsed=[(reverse.get(e['source'].rsplit('/',1)[0]),reverse.get(e['destination'].rsplit('/',1)[0]),e['destination'].rsplit('/',1)[1],e['delay_ns']) for e in p['edges']]
                    if not any(a==src and (b,pin) in sinks for a,b,pin,d in parsed):continue
                    potential=sum(d for a,b,pin,d in parsed if a in copied and (b in copied or a==src and (b,pin) in sinks))
                    relief[p['path']]=min(deficit(p),potential)
                if not relief:continue
                growth=change['lut_growth']+change.get('register_growth',0)+change.get('mux_growth',0)
                worst=min(p['slack_ns'] for p in witnesses if p['path'] in relief)
                benefit=sum(relief.values())/(1+.02*growth)
                score=(-benefit,worst,growth,src,tuple(sinks)) if coverage else (worst,-benefit,growth,src,tuple(sinks))
                options.append((score,nd,np,dict(change,launch=launch,covered_paths=sorted(relief),relief_potential_ns=relief,
                    ranking_scope='Old measured wire potential only; new input load, clock, route and hold effects not predicted')))
        if not options:continue
        chosen=None
        for score,nd,np,choice in sorted(options,key=lambda x:x[0]):
            if cluster_regions and cells[choice['source']]['type'] in LUTS|{'DFFRE','DFFSE'}:
                try:
                    nd,np,clustered=region(changed,placed,device,choice['source'],choice['sinks'],depth,max_registers=12,
                        allowed_registers={reverse[launch]} if launch_frontier else None,cluster_region=True,protected_cells=protected_cells)
                    choice.update(clustered)
                except ValueError as e:
                    rejections.append(dict(launch=launch,source=choice['source'],error=str(e),stage='selected_region_legalization'));continue
            chosen=nd,np,choice;break
        if chosen is None:continue
        changed,placed,choice=chosen;selected.append(choice)
        used_sources.add(choice['source']);used_sinks.update(map(tuple,choice['sinks']))
        for clone,parent in choice.get('register_replicas',{}).items():
            # Keep the induction certificate relative to the original state,
            # even if a later region encounters a previously copied frontier.
            while parent in aliases:parent=aliases[parent]
            aliases[clone]=parent
    return changed,placed,dict(selected=selected,rejections=rejections,register_replicas=aliases,target_mhz=target,
        target_reported_paths=sum(len(v) for v in by_launch.values()),selected_launches=len(selected),clock_replan_required=bool(aliases) or any(c.get('clocks_changed') for c in selected),
        coverage_policy=coverage,macro_legalization=legalize,launch_only_state_frontier=launch_frontier,clustered_regions=cluster_regions,allow_state_roots=allow_state_roots,
        selection_scope='One physical cone per selected launch group; does not imply target closure')


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for n in ('base','routes','sta','out'):p.add_argument('--'+n,type=Path,required=True)
    p.add_argument('--target-mhz',type=float,default=90);p.add_argument('--groups',type=int,default=5);p.add_argument('--depth',type=int,default=4)
    p.add_argument('--coverage',action='store_true');p.add_argument('--legalize-macros',action='store_true');p.add_argument('--launch-frontier',action='store_true');p.add_argument('--cluster-regions',action='store_true');p.add_argument('--allow-state-roots',action='store_true')
    p.add_argument('--pin-feedback',type=Path);p.add_argument('--protect-slack',type=float,default=.5);p.add_argument('--protect-target-mhz',type=float);a=p.parse_args()
    if not 1<=a.groups<=8 or not 1<=a.depth<=6 or not 0<a.target_mhz<=250:p.error('Bounded candidate limits exceeded')
    if a.protect_target_mhz is not None and not a.pin_feedback:p.error('Target protection requires checked pin feedback')
    start=time.monotonic()
    from workspace_budget import reserve
    reserve(ROOT,150_000_000);a.out.mkdir(parents=True,exist_ok=False)
    from ownpr_critical_tree_rebuild import verify_feedback
    verify_feedback(a.base,a.routes,a.sta)
    import re
    period=float(re.search(r'create_clock\s+-name\s+clk\s+-period\s+([\d.]+)',read_text(a.sta/'probe.sdc'))[1])
    original=load_json(a.base/'packed.json');layout=load_json(a.base/'placement.json')
    reverse={v:k for k,v in load_json(a.sta/'name_map.json')['instances'].items()}
    protected=set();protection=None
    if a.pin_feedback:
        from ownpr_pin_feedback import load
        from ownpr_feedback_transport import protected_placement_cells
        budgets,info=load(a.pin_feedback,a.sta);window=a.protect_slack;conversion=None
        if a.protect_target_mhz is not None:
            from ownpr_target_protection import from_capture
            window,conversion=from_capture(a.sta,a.protect_target_mhz,a.protect_slack)
        protected,protection=protected_placement_cells(original,layout,budgets,window);protection['capture']=info;protection['target_conversion']=conversion
    changed,placed,plan=propose(original,layout,Device(),diagnose(read_text(a.sta/'probe.tr')),reverse,period,a.target_mhz,a.groups,a.depth,a.coverage,a.legalize_macros,a.launch_frontier,a.cluster_regions,a.allow_state_roots,protected)
    plan['placement_protection']=protection
    (a.out/'plan.json').write_text(json.dumps(plan,indent=2))
    if not plan['selected']:raise ValueError('No supported regional candidates')
    if plan['register_replicas']:
        from ownpr_register_equivalence import prove_replicas
        proof=prove_replicas(original,changed,plan['register_replicas'],a.out/'proof',120)
    else:
        from native_boundary_cec import prove
        proof=prove(original,changed,a.out/'proof',120)
    for name,value in [('packed.json',changed),('placement.json',placed),('proof.json',proof)]:
        (a.out/name).write_text(json.dumps(value,indent=2))
    sources=[Path(__file__),ROOT/'scripts/ownpr_regional_replication.py',ROOT/'scripts/ownpr_macro_replication.py',ROOT/'scripts/ownpr_register_equivalence.py',ROOT/'scripts/ownpr_macro_legalize.py',ROOT/'scripts/ownpr_feedback_transport.py',ROOT/'scripts/ownpr_target_protection.py']
    for src in sources:shutil.copyfile(src,a.out/(src.name+'.txt'))
    meta=dict(status='placed',netlist_sha256=sha(a.out/'packed.json'),placement_sha256=sha(a.out/'placement.json'),
        source_netlist_sha256=sha(a.base/'packed.json'),source_placement_sha256=sha(a.base/'placement.json'),source_sta_sha256=sha(a.sta/'probe.tr'),
        proof=proof,source_sha256={src.name:sha(a.out/(src.name+'.txt')) for src in sources},wall_seconds=time.monotonic()-start,
        vendor_pnr_executed=False,timing_signoff=False,physical_legality_complete=False)
    (a.out/'manifest.json').write_text(json.dumps(meta,indent=2))
    print(json.dumps(dict(selected=[dict(source=c['source'],sinks=c['sinks'],launch=c['launch'],paths=len(c['covered_paths']),lut_growth=c['lut_growth'],register_growth=c.get('register_growth',0)) for c in plan['selected']],wall_seconds=meta['wall_seconds']),indent=2))


if __name__=='__main__':main()
