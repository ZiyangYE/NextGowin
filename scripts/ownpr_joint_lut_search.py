"""Measured cut remapping, LUT placement and incremental routing transactions."""
import argparse,copy,json,os,shutil,subprocess,sys,time
from pathlib import Path
from gowin_probe import ROOT,prepare
from ownpr_image_storage import load_json,sha


def implement(design,layout,cut,late,sites,tag):
    from ownpr_multilevel_cut import Cuts
    verified=Cuts(design,k=5,depth=6).function(cut['root'],cut['evaluation_boundary'])
    if verified!=cut:raise ValueError('Changed cut certificate')
    boundary=list(cut['cut_bits']);width=len(boundary)
    if not 1<=width<=5:raise ValueError('Unsupported network width')
    if isinstance(late,list):
        if set(late)!=set(boundary) or len(late)!=len(boundary):raise ValueError('Invalid input timing order')
        boundary=list(late)
    elif late in boundary:boundary.remove(late);boundary.append(late)
    truth=int(cut['truth_hex'],16);word=0
    for assignment in range(1<<width):
        values={b:assignment>>i&1 for i,b in enumerate(boundary)};address=sum(values[b]<<i for i,b in enumerate(cut['cut_bits']));word|=(truth>>address&1)<<assignment
    new=copy.deepcopy(design);placed=copy.deepcopy(layout);cells=new['modules']['top']['cells'];root=cut['root'];output=cells[root]['connections']['F'][0]
    root_metadata={k:copy.deepcopy(v) for k,v in cells[root].items() if k not in ('type','parameters','attributes','port_directions','connections')}
    grounds=[c['connections']['G'][0] for c in cells.values() if c['type']=='GND']
    def cell(bits,out,truth,attributes):
        padding=4-len(bits)
        if padding and not grounds:raise ValueError('LUT padding requires an existing physical ground driver')
        word=sum(((truth>>(a>>padding))&1)<<a for a in range(16));bits=([grounds[0]]*padding if padding else [])+bits
        return dict(type='LUT4',parameters={'INIT':format(word,'016b')},attributes=attributes,
            port_directions={**{f'I{i}':'input' for i in range(4)},'F':'output'},connections={**{f'I{i}':[b] for i,b in enumerate(bits)},'F':[out]})
    attributes=copy.deepcopy(cells[root].get('attributes',{}));added=[]
    if width<=4:cells[root]=cell(boundary,output,word,attributes)
    else:
        values=[b for c in cells.values() for bs in c['connections'].values() for b in bs]+[b for r in new['modules']['top'].get('netnames',{}).values() for b in r['bits']]+[b for r in new['modules']['top']['ports'].values() for b in r['bits']]
        first=1+max(b for b in values if isinstance(b,int));bits=[]
        for i in range(2):
            name=f'ng_joint_{tag}_{root}_leaf_{i}'
            if name in cells:raise ValueError('Generated LUT name collision')
            bit=first+i;bits.append(bit);cells[name]=cell(boundary[:4],bit,(word>>(16*i))&65535,{})
            r,c,s=sites[i+1];placed['clusters'][name]=dict(lut=name,ff=None,row=r,column=c,slot=s);added.append(name)
        cells[root]=cell([*bits,boundary[4]],output,0xCA,attributes)
    cells[root].update(root_metadata)
    r,c,s=sites[0];placed['clusters'][root].update(row=r,column=c,slot=s)
    return new,placed,dict(root=root,cut=cut,ordered_boundary=boundary,sites=sites,added_luts=added,ordinary_lut_network=True,interface_cycles_changed=False)


def proposals(design,layout,device,metrics,graphs,limit=24,critical_window=1.):
    from ownpr_multilevel_cut import Cuts
    from ownpr_critical_chain_move import exclusive_tiles,locations
    from ownpr_state_safety import protected_cells
    from ownpr_lut_graph_feedback import LUTS
    cells=design['modules']['top']['cells'];cuts=Cuts(design,k=5,depth=6,limit=48);excluded=exclusive_tiles(layout);protected=protected_cells(design);points=locations(layout)
    private={b for c in cells.values() if c['type'].startswith('MUX2_LUT') for p in ('I0','I1') for b in c['connections'][p]}
    outputs={b:n+'/'+p for n,c in cells.items() for p,bs in c['connections'].items() if c['port_directions'][p]=='output' for b in bs}
    arrivals=metrics['setup']['arrival'];slacks=metrics['setup']['slack'];occupied={(r['row'],r['column'],r['slot']) for r in layout['clusters'].values()};found=[]
    roots=sorted((n for n,c in cells.items() if c['type'] in LUTS and n not in protected and n+'/F' in slacks and min(slacks[n+'/F'])<=metrics['setup']['wns']+critical_window and (layout['clusters'][n]['row'],layout['clusters'][n]['column']) not in excluded),key=lambda n:(min(slacks[n+'/F']),n))[:64]
    for root in roots:
        candidates=[]
        for boundary in cuts.enumerate(cells[root]['connections']['F'][0]):
            if boundary&private:continue
            try:cut=cuts.function(root,boundary)
            except ValueError:continue
            if cut['interior_count']<2 or not 1<=cut['cut_width']<=5 or any(b not in outputs or outputs[b] not in arrivals for b in cut['cut_bits']):continue
            late=max(cut['cut_bits'],key=lambda b:max(arrivals[outputs[b]]));optimistic=max(arrivals[root+'/F'])-(max(max(arrivals[outputs[b]]) for b in cut['cut_bits'])+.55*(1 if cut['cut_width']<=4 else 2))
            if optimistic<.1:continue
            candidates.append((-optimistic,cut,late))
        for _,cut,late in sorted(candidates,key=lambda x:(x[0],x[1]['cut_width'],x[1]['cut_bits']))[:2]:
            row=layout['clusters'][root];old=(row['row'],row['column'],row['slot']);driver=outputs[late].rsplit('/',1)[0];anchor=points.get(driver,old[:2])
            centers=[old[:2]]
            if not row.get('ff'):centers.append((round((old[0]+anchor[0])/2),round((old[1]+anchor[1])/2)))
            for center in dict.fromkeys(centers):
                vacancies=[(r,c,s) for r in range(max(0,center[0]-2),min(device.rows,center[0]+3)) for c in range(max(0,center[1]-2),min(device.cols,center[1]+3)) if device.grid[r][c]==17 and (r,c) not in excluded for s in range(8) if (r,c,s) not in occupied or (r,c,s)==old]
                if center==old[:2]:site=old
                elif vacancies:site=min(vacancies,key=lambda s:(abs(s[0]-center[0])+abs(s[1]-center[1]),s[2]>=6,s))
                else:continue
                leaves=sorted((s for s in vacancies if s!=site),key=lambda s:(abs(s[0]-site[0])+abs(s[1]-site[1]),s[2]>=6,s))[:2]
                if cut['cut_width']==5 and len(leaves)!=2:continue
                sites=[site]+(leaves if cut['cut_width']==5 else [])
                order=sorted(cut['cut_bits'],key=lambda bit:(max(arrivals[outputs[bit]]),bit))
                found.append((min(slacks[root+'/F']),_,root,cut,order,sites))
    # Round-robin roots so one near-tied critical cone cannot fill the beam.
    queues={};ordered=[]
    for row in sorted(found,key=lambda x:(x[0],x[1],x[2])):queues.setdefault(row[2],[]).append(row)
    while any(queues.values()) and len(ordered)<limit:
        for root in queues:
            if queues[root] and len(ordered)<limit:ordered.append(queues[root].pop(0))
    return ordered


def batch_transform(design,layout,source,tag):
    """Compose independent measured cuts, then reroute/re-evaluate jointly."""
    from ownpr_multilevel_cut import Cuts
    rows=[r for r in load_json(source/'trials.json') if r.get('iteration')==0 and r.get('admissible') and r.get('change')]
    rows.sort(key=lambda r:(-r['setup']['wns'],-r['setup']['tns']))
    chosen=[];roots=set();interior=set();occupied=set();nd=design;nl=layout
    for row in rows:
        change=row['change'];cut=change['cut'];root=cut['root'];sites=list(map(tuple,change['sites']))
        if root in interior or roots&set(cut['interior']) or occupied&set(sites):continue
        current=Cuts(nd,k=5,depth=6).function(root,cut['evaluation_boundary'])
        nd,nl,updated=implement(nd,nl,current,change['ordered_boundary'],sites,str(tag)+'_'+str(len(chosen)))
        chosen.append(updated);roots.add(root);interior.update(cut['interior']);occupied.update(sites)
        if len(chosen)>=8:break
    if len(chosen)<2:raise ValueError('No compatible multi-cone measured batch')
    return nd,nl,dict(batch=True,individuals=chosen,source_trials_sha256=sha(source/'trials.json'),interface_cycles_changed=False)


def worker(a):
    from native_device_session import NativeDeviceSession
    from ownpr_device import Device
    from ownpr_timing_graph import TimingRoutingGraph
    from ownpr_clock_graph import ClockGraph
    from ownpr_clock_partition import partition as clock_partition
    from ownpr_route_design import bind
    from ownpr_incremental_routes import signature,partition,retained_branches
    from ownpr_router import route,validate
    from ownpr_expand_routes import expand
    from ownpr_cell_timing import NativeCellTiming
    from ownpr_lut_graph_feedback import FixedBoundaryLutFeedback
    from ownpr_critical_chain_move import apply as validate_placement
    from ownpr_alias_feedback import admissible,preserves_timing_guards
    from ownpr_model_reference import load_reference,validate_reconstruction
    from native_boundary_cec import prove
    from audit_native_wire_delays import f32
    start=time.monotonic();deadline=start+a.seconds;native=NativeDeviceSession(a.out)
    original=load_json(a.placement/'packed.json');original_layout=load_json(a.placement/'placement.json');clock=load_json(a.routes/'clock_tree.json');hard=load_json(a.routes/'hard_endpoint_audit.json') if original_layout.get('hard_clusters') else None
    if hard and sha(a.routes/'hard_endpoint_audit.json')!=load_json(a.routes/'manifest.json')['hard_endpoint_audit_sha256']:raise ValueError('Hard endpoint reference changed')
    reference,base_graphs,model=load_reference(a.model,a.placement,a.routes,a.wire)
    graph=TimingRoutingGraph(Device(),ROOT/'results/native_rrg_001',native);graph.heuristic_scale=.03;graph.root_arrival_weight=1.;graph.guided_corridor=True;graph.guide_edge_delay=.05
    clock_model=ClockGraph.for_tree(clock)
    reserved={graph.node(r-1,c-1,graph.device.names[i]) for row in clock['parents'] for r,c,i in [row['node']] if min(r,c)>=1 and i in graph.device.names}
    from ownpr_ioreg_macros import reserved_points
    reserved.update(graph.node(r-1,c-1,graph.device.names[i]) for r,c,i in reserved_points(original,original_layout))
    def binding(design,layout):
        logical=[];allnets,static=bind(design,layout,graph,hard_bindings=hard,logical_sinks=logical);nets,_=clock_partition(graph,allnets,clock,clock_model)
        return nets,allnets,static,logical
    design=original;layout=original_layout;nets,allnets,static,logical=binding(design,layout);routes=load_json(a.routes/'routes.json')
    if {n['name']:signature(n) for n in nets}!={n['name']:signature(n) for n in load_json(a.routes/'nets.json')}:raise ValueError('Source physical binding differs')
    validate(graph,nets,routes);raw=load_json(a.wire/'wire_delays.json')
    measurements={name:{graph.node(s['node'][0]-1,s['node'][1]-1,graph.device.names[s['node'][2]]):s['delays_by_selector'][1][2:4]+s['delays_by_selector'][2][:2] for s in row['sinks']} for name,row in raw['nets'].items()}
    feedback_type=FixedBoundaryLutFeedback
    if a.operator in ('carry','cone'):
        from ownpr_carry_feedback import FixedBoundaryCarryFeedback
        feedback_type=FixedBoundaryCarryFeedback
    if a.macro_input_isolation:
        from ownpr_shared_feedback import SharedDagFeedback
        feedback_type=SharedDagFeedback
    if a.wide_mapping:
        from ownpr_wide_mapping_feedback import WideMappingFeedback
        feedback_type=WideMappingFeedback
    if a.existing_wide_remap:
        from ownpr_existing_wide_remap import ExistingWideRemapFeedback
        feedback_type=ExistingWideRemapFeedback
    if a.general_wide_recompile:
        from ownpr_general_wide_recompile import GeneralWideRecompileFeedback
        feedback_type=GeneralWideRecompileFeedback
    if a.cross_macro_recompile:
        from ownpr_cross_macro_recompile import CrossMacroRecompileFeedback
        feedback_type=CrossMacroRecompileFeedback
    if a.wide_trust_region:
        from ownpr_wide_trust_region import WideTrustRegionFeedback
        feedback_type=WideTrustRegionFeedback
    feedback=feedback_type(model,base_graphs,original,original_layout,static,NativeCellTiming(),graph.device)
    feedback.joint_mapping_and_move=a.joint_wide_move_map
    feedback.group_moves=a.wide_group_move
    feedback.ordinary_group_moves=a.group_ordinary_luts
    feedback.trust_radius=a.trust_radius
    graphs=feedback.rebuild(design,layout,logical,measurements,static);metrics=feedback.metrics(graphs)
    complete_baseline=validate_reconstruction(model,base_graphs,graphs);hold_reference=metrics
    baseline_checks={}
    for name in ('setup','hold'):
        delta=abs(metrics[name]['wns']-reference[name]['wns']);baseline_checks[name]=dict(wns_error_ns=delta,expected=reference[name],actual={k:metrics[name][k] for k in ('wns','tns','violations')})
        old={(e['source'],e['destination'],e['kind']):e for e in base_graphs[name]['edges']};new={(e['source'],e['destination'],e['kind']):e for e in graphs[name]['edges']}
        baseline_checks[name]['edge_differences']=[dict(key=k,old=old.get(k),new=new.get(k)) for k in set(old)|set(new) if k not in old or k not in new or old[k]['delay']!=new[k]['delay'] or set(map(tuple,old[k]['transitions']))!=set(map(tuple,new[k]['transitions']))][:30]
    (a.out/'baseline_diagnostic.json').write_text(json.dumps(baseline_checks,indent=2))
    if any(r['wns_error_ns']>.00001 or abs(r['actual']['tns']-r['expected']['tns'])>.002 for r in baseline_checks.values()):raise ValueError('LUT graph baseline does not reproduce validated timing')
    (a.out/'baseline_validation.json').write_text(json.dumps(dict(status='reproduced',full_graph_comparison=complete_baseline,wns_error_ns=baseline_checks,metrics={n:{k:metrics[n][k] for k in ('wns','tns','violations')} for n in metrics}),indent=2))
    def measure(net,record):
        data=expand(graph,[net],dict(nets={net['name']:record}),native.fanouts)['nets'][net['name']];parents={tuple(p['node']):tuple(p['parent']) if p['parent'] else None for p in data['parents']}
        with native.parent_tree(parents):values={p:(native.delay(p,1),native.delay(p,2)) for p in parents}
        result={}
        for s in data['sinks']:
            sums=[]
            for selector,index in ((0,2),(0,3),(1,0),(1,1)):
                v=0.
                for point in s['path_sink_to_source']:v=f32(v+values[tuple(point)][selector][index])
                sums.append(v)
            result[s['resource']]=sums
        return result
    seed_receipt=None
    if a.seed_from:
        sm=load_json(a.seed_from/'manifest.json');sp=a.seed_from/'placement'
        if (sm.get('status')!='routed' or sm.get('diagnostic_only') or sm.get('source_netlist_sha256')!=sha(a.placement/'packed.json')
                or sm.get('source_placement_sha256')!=sha(a.placement/'placement.json') or sm.get('source_routes_sha256')!=sha(a.routes/'routes.json')
                or sm.get('source_model_sha256')!=sha(a.model/'model.json') or sm.get('netlist_sha256')!=sha(sp/'packed.json')
                or sm.get('placement_sha256')!=sha(sp/'placement.json') or sha(a.seed_from/'clock_tree.json')!=sha(a.routes/'clock_tree.json')):
            raise ValueError('Warm search seed differs from the exact source/model')
        if hard and sha(a.seed_from/'hard_endpoint_audit.json')!=sha(a.routes/'hard_endpoint_audit.json'):raise ValueError('Seed hard endpoints differ')
        sd=load_json(sp/'packed.json');sl=load_json(sp/'placement.json');sn,sa,st,slogical=binding(sd,sl);sr=load_json(a.seed_from/'routes.json')
        feedback.admit(sd,sl,st);validate(graph,sn,sr)
        if {n['name']:signature(n) for n in sn}!={n['name']:signature(n) for n in load_json(a.seed_from/'nets.json')}:raise ValueError('Seed physical binding differs')
        oldnets={n['name']:n for n in nets};remeasured=[];smv={n:v for n,v in measurements.items() if n in {x['name'] for x in sn}}
        for net in sn:
            name=net['name']
            if name not in oldnets or signature(net)!=signature(oldnets[name]) or sr['nets'][name]!=routes['nets'][name]:smv[name]=measure(net,sr['nets'][name]);remeasured.append(name)
        sg=feedback.rebuild(sd,sl,slogical,smv,st);smetrics=feedback.metrics(sg)
        if not admissible(smetrics,metrics,hold_reference):raise ValueError('Measured warm seed fails original timing guards')
        seed_receipt=dict(manifest_sha256=sha(a.seed_from/'manifest.json'),routes_sha256=sha(a.seed_from/'routes.json'),remeasured_nets=remeasured,metrics={n:{k:smetrics[n][k] for k in ('wns','tns','violations')} for n in smetrics})
        design,layout,nets,allnets,static,logical,routes,measurements,graphs,metrics=sd,sl,sn,sa,st,slogical,sr,smv,sg,smetrics
    trials=[];selected=[];seen=set();frontier_audits=[];initial={n:{k:hold_reference[n][k] for k in ('wns','tns','violations')} for n in hold_reference}
    for iteration in range(a.rounds):
        best=None;evaluated=0
        if a.operator=='cone':
            from ownpr_path_cone import proposals as cone_proposals,carry_copies
            if a.existing_wide_remap or a.general_wide_recompile or a.cross_macro_recompile or a.wide_trust_region:
                if a.wide_trust_region:
                    from ownpr_wide_trust_region import proposals as existing_proposals
                elif a.cross_macro_recompile:
                    from ownpr_cross_macro_recompile import proposals as existing_proposals
                elif a.general_wide_recompile:
                    from ownpr_general_wide_recompile import proposals as existing_proposals
                else:
                    from ownpr_existing_wide_remap import proposals as existing_proposals
                frontier_audit=dict(iteration=iteration)
                offered=existing_proposals(design,layout,graph.device,metrics,graphs,feedback,32,frontier_audit,deadline=deadline-60)
                frontier_audits.append(frontier_audit)
                (a.out/'global_frontier.json').write_text(json.dumps(frontier_audits,indent=2))
            elif a.wide_mapping:
                from ownpr_wide_mapping import proposals as wide_proposals
                frontier_audit=dict(iteration=iteration)
                offered=wide_proposals(design,layout,graph.device,metrics,graphs,32,global_frontier=a.global_frontier,frontier_audit=frontier_audit)
                if a.global_frontier:
                    frontier_audits.append(frontier_audit)
                    (a.out/'global_frontier.json').write_text(json.dumps(frontier_audits,indent=2))
                if a.wide_batch_from:
                    from ownpr_wide_mapping import measured_batch as wide_batch
                    for maximum in (3,6):
                        try:
                            bd,bl,bc=wide_batch(design,layout,graph.device,a.wide_batch_from,maximum)
                            offered.insert(0,(metrics['setup']['wns'],0.,'__wide_batch_'+str(maximum)+'__',bl,bc,bd))
                        except ValueError:pass
            elif a.critical_cover:
                from ownpr_critical_cover import proposals as cover_proposals
                cover_audit=dict(iteration=iteration)
                offered=cover_proposals(design,layout,graph.device,metrics,graphs,32,cover_audit)
                frontier_audits.append(cover_audit)
                (a.out/'global_frontier.json').write_text(json.dumps(frontier_audits,indent=2))
            elif a.shared_dag:
                from ownpr_shared_dag import proposals as shared_proposals
                offered=shared_proposals(design,layout,graph.device,metrics,graphs,32,a.macro_input_isolation)
            else:offered=carry_copies(design,layout,graph.device,metrics,graphs,1)+cone_proposals(design,layout,graph.device,metrics,graphs,48,a.critical_window)
            if a.cone_batch_from:
                from ownpr_path_cone import measured_batch
                try:
                    bd,bl,bc=measured_batch(design,layout,a.cone_batch_from,metrics)
                    offered.insert(0,(metrics['setup']['wns'],0.,'__measured_path_batch__',bl,bc,bd))
                except ValueError:pass
            scheduled=[(w,g,head,dict(cut_bits=[],cut_width=0,interior_count=len(change['cells'])),(candidate_design,candidate),change) for w,g,head,candidate,change,candidate_design in offered]
        elif a.operator=='carry':
            from ownpr_carry_translate import proposals as carry_proposals
            scheduled=[(w,g,head,dict(cut_bits=[],cut_width=0,interior_count=len(change['cells'])),(candidate_design,candidate),change) for w,g,head,candidate,change,candidate_design in carry_proposals(design,layout,graph.device,metrics,graphs,32)]
        else:scheduled=[(0,0,'__batch__',dict(cut_bits=[],cut_width=0,interior_count=0),[],[])] if a.batch_from else ([(0,0,'__diagnostic__',dict(cut_bits=[],cut_width=0,interior_count=0),[],[])] if a.diagnostic_from else proposals(design,layout,graph.device,metrics,graphs,192,a.critical_window))
        for proposal in scheduled:
            if len(trials)>=a.trials or evaluated>=a.per_round or time.monotonic()+60>deadline:break
            _,_,root,cut,late,sites=proposal
            key=(root,tuple(sites['destination']),sites.get('variant'),tuple(sites['neighbor_luts'])) if a.operator in ('carry','cone') else (root,tuple(cut['cut_bits']),tuple(map(tuple,sites)))
            if key in seen:continue
            seen.add(key);evaluated+=1;row=dict(iteration=iteration,root=root,width=cut['cut_width'],interior_count=cut['interior_count'],sites=sites,admissible=False);stamp=time.monotonic()
            try:
                if a.operator in ('carry','cone'):(nd,nl),change=late,sites
                elif a.diagnostic_from:
                    source_row=load_json(a.diagnostic_from/'trials.json')[a.diagnostic_index];source_change=source_row['change']
                    nd,nl,change=implement(design,layout,source_change['cut'],source_change['ordered_boundary'],source_change['sites'],'diag_'+str(a.diagnostic_index))
                else:nd,nl,change=batch_transform(design,layout,a.batch_from,len(trials)) if a.batch_from else implement(design,layout,cut,late,sites,len(trials))
                moved={n:[r['row'],r['column'],r['slot']] for n,r in nl['clusters'].items() if n in layout['clusters'] and (r['row'],r['column'],r['slot'])!=(layout['clusters'][n]['row'],layout['clusters'][n]['column'],layout['clusters'][n]['slot'])}
                if a.operator in ('carry','cone'):
                    from ownpr_ff_configuration import compile_initial
                    compile_initial(nd,nl)
                    from ownpr_mux_macros import validate as validate_mux
                    from ownpr_carry_macros import validate as validate_carry
                    validate_mux(nd,nl,graph.device);validate_carry(nd,nl,graph.device)
                    if nl.get('hard_clusters'):
                        from ownpr_hard_architecture import HardArchitecture
                        HardArchitecture().validate(nd,nl,graph.device)
                else:validate_placement(nd,nl,graph.device,dict(sites=moved))
                ns,na,nt,nlogical=binding(nd,nl);changed,frozen,blocked,info=partition(graph,nets,routes,ns,reserved)
                feedback.admit(nd,nl,nt)
                if not changed:raise ValueError('No physical change')
                seeds,_=retained_branches(graph,nets,routes,changed,ns,blocked)
                if a.reuse_clone_branches:
                    from ownpr_clone_route_seeds import reuse_by_source
                    seeds,row['clone_branch_reuse']=reuse_by_source(graph,nets,routes,changed,ns,blocked,seeds)
                route_inputs=changed
                if a.operator=='cone':
                    from ownpr_path_cone import prioritize
                    route_inputs=prioritize(changed,nlogical,metrics,change)
                    row['routing_policy']='Full-graph slack weights per physical sink; original binding metadata retained'
                partial=route(graph,route_inputs,max_iterations=12 if a.operator in ('carry','cone') else 8,timeout=min(120 if change.get('operator')=='wide_mapping_batch' else 100 if change.get('operator')=='path_cone_batch' else 60 if a.operator in ('carry','cone') else 100 if a.batch_from else 18,deadline-time.monotonic()-25),max_expansions=350000 if change.get('operator')=='wide_mapping_batch' else 250000 if a.operator in ('carry','cone') else 180000 if a.batch_from else 120000,blocked_nodes=blocked,initial_trees=seeds)
                if partial['status']!='routed':raise ValueError('Incremental candidate routing failed')
                nr=dict(status='routed',nets={**frozen,**partial['nets']});validate(graph,ns,nr)
                nm={n:v for n,v in measurements.items() if n in {x['name'] for x in ns}}
                for net in changed:nm[net['name']]=measure(net,nr['nets'][net['name']])
                ng=feedback.rebuild(nd,nl,nlogical,nm,nt);metric=feedback.metrics(ng)
                row.update(change=change,changed_nets=len(changed),setup={k:metric['setup'][k] for k in ('wns','tns','violations')},hold={k:metric['hold'][k] for k in ('wns','tns','violations')},admissible=admissible(metric,metrics,hold_reference))
                row['diagnostic_guards_passed']=preserves_timing_guards(metric,metrics,hold_reference)
                if row['admissible'] or a.diagnostic_from and row['diagnostic_guards_passed']:
                    score=(-metric['setup']['wns'],-metric['setup']['tns'],len(nd['modules']['top']['cells']))
                    if best is None or score<best[0]:best=(score,nd,nl,ns,na,nt,nlogical,nr,nm,ng,metric,row)
            except (ValueError,TimeoutError) as e:row['error']=str(e)
            row['wall_seconds']=time.monotonic()-stamp;trials.append(row);(a.out/'trials.json').write_text(json.dumps(trials,indent=2));print('LUT_TRIAL',len(trials),row.get('setup'),row.get('error'),flush=True)
        if best is None:break
        _,design,layout,nets,allnets,static,logical,routes,measurements,graphs,metrics,row=best;selected.append(row)
        print('LUT_COMMIT',len(selected),row['setup'],flush=True)
    for audit in frontier_audits:
        rows=[r for r in trials if r['iteration']==audit['iteration']]
        measured={r['root'] for r in rows if 'setup' in r}
        measured.update(c['root'] for r in rows if 'setup' in r for c in r.get('change',{}).get('individuals',[]))
        audit['attempted_roots']=sorted({r['root'] for r in rows})
        audit['measured_roots']=sorted(measured)
        audit['physical_candidates_measured']=bool(measured)
        if 'selected' in audit:
            audit['fanin_groups_represented_by_measured_roots']=sorted({g for row in audit['selected'] if row['root'] in measured for g in row['reachable_groups']})
        elif a.critical_cover:
            audit['measured_copy_roots']=sorted({c['root'] for r in rows if 'setup' in r for c in r.get('change',{}).get('copies',[])})
            audit['coverage_is_proposal_only']=True
        else:raise ValueError('Unknown frontier audit schema')
    if frontier_audits:(a.out/'global_frontier.json').write_text(json.dumps(frontier_audits,indent=2))
    result=dict(status='no_admissible_joint_lut_candidate',trials=len(trials),measured_candidates=sum('setup' in r for r in trials),candidate_errors=sum('error' in r for r in trials),accepted_transactions=0 if a.diagnostic_from else len(selected),diagnostic_only=bool(a.diagnostic_from),source_netlist_sha256=sha(a.placement/'packed.json'),source_placement_sha256=sha(a.placement/'placement.json'),source_routes_sha256=sha(a.routes/'routes.json'),source_model_sha256=sha(a.model/'model.json'),baseline_metrics=initial,current_metrics={n:{k:metrics[n][k] for k in ('wns','tns','violations')} for n in metrics},wall_seconds=time.monotonic()-start,vendor_pnr_executed=False,open_chip_database_used=False,timing_signoff=False)
    # Preserve the policy for downstream checkpoint routing. Older manifests
    # omitted it, causing the receiver to silently revert to different costs.
    result.update(search_bias=graph.heuristic_scale/.001,heuristic_scale=graph.heuristic_scale,
        root_arrival_weight=graph.root_arrival_weight,guided_corridor=graph.guided_corridor,
        guide_edge_delay_ns_surrogate=graph.guide_edge_delay)
    result['operator']=a.operator
    result['proposal_family']='critical_pin_cover' if a.critical_cover else 'wide_trust_region' if a.wide_trust_region else 'cross_macro_recompile' if a.cross_macro_recompile else 'general_wide_recompile' if a.general_wide_recompile else 'existing_wide_remap' if a.existing_wide_remap else 'wide_mapping' if a.wide_mapping else 'shared_multioutput_dag' if a.shared_dag else a.operator
    result['existing_wide_remap']=a.existing_wide_remap
    result['general_wide_recompile']=a.general_wide_recompile
    result['cross_macro_recompile']=a.cross_macro_recompile
    result['wide_trust_region']=a.wide_trust_region
    result['joint_wide_move_map']=a.joint_wide_move_map
    result['wide_group_move']=a.wide_group_move
    result['group_ordinary_luts']=a.group_ordinary_luts
    result['trust_radius']=a.trust_radius
    result['wide_mapping']=a.wide_mapping
    result['global_frontier']=a.global_frontier
    if frontier_audits:result['global_frontier_audit_sha256']=sha(a.out/'global_frontier.json')
    if a.wide_mapping or a.existing_wide_remap or a.general_wide_recompile or a.cross_macro_recompile or a.wide_trust_region:result['mux_timing_calibration_sha256']=feedback.mux_library.sha256
    result['macro_input_isolation']=a.macro_input_isolation
    result['reuse_clone_branches']=a.reuse_clone_branches
    if seed_receipt:result['warm_search_seed']=seed_receipt
    if a.operator in ('carry','cone'):result['alu_timing_calibration_sha256']=feedback.alu_library.sha256
    if selected or seed_receipt:
        proof=prove(original,design,a.out/'cec',120)
        if not proof and original!=design or not all(r['cec']['equivalent'] for r in proof):raise ValueError('Final fixed-state combinational CEC failed')
        placement=a.out/'placement';placement.mkdir();(placement/'packed.json').write_text(json.dumps(design,indent=2));(placement/'placement.json').write_text(json.dumps(layout,indent=2))
        pm=dict(status='placed',netlist_sha256=sha(placement/'packed.json'),placement_sha256=sha(placement/'placement.json'),source_netlist_sha256=sha(a.placement/'packed.json'),proof=dict(status='fixed_state_combinational_cec_proved',checks=proof,logical_netlist_identical=original==design),clock_endpoints_unchanged=True,state_and_hard_resources_unchanged=True,lut_delta=len(design['modules']['top']['cells'])-len(original['modules']['top']['cells']),register_delta=0,timing_signoff=False)
        (placement/'manifest.json').write_text(json.dumps(pm,indent=2))
        for name,data in [('routes.json',routes),('nets.json',nets),('all_nets.json',allnets),('static_connections.json',static),('selected.json',selected)]: (a.out/name).write_text(json.dumps(data,indent=2))
        for name in ('clock_tree.json',)+(('hard_endpoint_audit.json',) if hard else ()):shutil.copyfile(a.routes/name,a.out/name)
        feedback_dir=a.out/'feedback';feedback_dir.mkdir();output=dict(model,late=graphs['setup'],early=graphs['hold']);(feedback_dir/'model.json').write_text(json.dumps(output,separators=(',',':')))
        result.update(pm,status='routed',net_count=len(nets),connectivity_audit=validate(graph,nets,routes),model_sha256=sha(feedback_dir/'model.json'),clock_tree_sha256=sha(a.out/'clock_tree.json'))
        if hard:result['hard_endpoint_audit_sha256']=sha(a.out/'hard_endpoint_audit.json')
    result['wall_seconds']=time.monotonic()-start;(a.out/'manifest.json').write_text(json.dumps(result,indent=2))


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for n in ('placement','routes','wire','model','out'):p.add_argument('--'+n,type=Path,required=True)
    p.add_argument('--batch-from',type=Path,help='Compose first-round admissible measured cuts from this completed search')
    p.add_argument('--seed-from',type=Path,help='Continue a completed measured search from this exact source/model; remeasure changed trees and reprove final logic')
    p.add_argument('--cone-batch-from',type=Path,help='Generate a fresh routed transaction composing previously admissible physical path cones')
    p.add_argument('--shared-dag',action='store_true',help='Shared multi-output DAG and old-region side-load isolation proposals')
    p.add_argument('--critical-cover',action='store_true',help='Atomic exact-copy sets covering all paths in a conservative near-critical pin DAG')
    p.add_argument('--macro-input-isolation',action='store_true',help='Allow exact equivalent root copies to feed fixed wide-LUT external inputs')
    p.add_argument('--reuse-clone-branches',action='store_true',help='Reuse common physical branches even when a clone gives the old source a new net name')
    p.add_argument('--wide-mapping',action='store_true',help='Joint isolated LUT5/6/7 remapping, placement and routing under fixed state/clock boundaries')
    p.add_argument('--existing-wide-remap',action='store_true',help='Whole-macro Shannon remapping under unchanged sites, state, output identity and dedicated topology')
    p.add_argument('--general-wide-recompile',action='store_true',help='Exact branch-specific cofactor recompilation including reduced/noncanonical existing wide macros')
    p.add_argument('--cross-macro-recompile',action='store_true',help='Exact upstream-logic absorption into fixed wide geometry with global-cut proof and loaded rerouting')
    p.add_argument('--wide-trust-region',action='store_true',help='Measured local translations of existing unshared wide macros with all registers/clocks fixed')
    p.add_argument('--joint-wide-move-map',action='store_true',help='Joint exact cofactor recompilation and wide-macro translation, accepted only after measured routing')
    p.add_argument('--wide-group-move',action='store_true',help='Joint measured placement of connected wide-macro pairs/triples, with fixed state and exact loaded-route feedback')
    p.add_argument('--group-ordinary-luts',action='store_true',help='Include unchanged unpaired ordinary LUTs occupying private CFUs in group moves')
    p.add_argument('--trust-radius',type=int,choices=(6,12),default=6,help='Reference-anchored CFU search radius; both settings require exact native type17 geometry and routed timing')
    p.add_argument('--global-frontier',action='store_true',help='Select wide-mapping roots across all endpoint roles and spatial regions instead of a worst-slack window')
    p.add_argument('--wide-batch-from',type=Path,help='Add joint proposals composing disjoint previously measured wide cuts')
    p.add_argument('--operator',choices=('lut','carry','cone'),default='lut')
    p.add_argument('--diagnostic-from',type=Path,help='Isolated export-only replay of one measured non-degrading candidate; never a delivered optimization')
    p.add_argument('--diagnostic-index',type=int,default=0)
    p.add_argument('--critical-window',type=float,default=1.,help='Through-pin setup window above current global WNS; includes clean designs')
    p.add_argument('--seconds',type=int,default=1000);p.add_argument('--trials',type=int,default=24);p.add_argument('--rounds',type=int,default=2);p.add_argument('--per-round',type=int,default=12);p.add_argument('--worker',action='store_true');a=p.parse_args()
    if not 90<=a.seconds<=1800 or not 1<=a.trials<=48 or not 1<=a.rounds<=3 or not 1<=a.per_round<=24:p.error('Invalid bounded joint search')
    if not .01<=a.critical_window<=5:p.error('Invalid critical setup window')
    if a.operator in ('carry','cone') and (a.batch_from or a.diagnostic_from):p.error('Physical cone/carry search does not use LUT-only diagnostic/batch replay')
    if a.shared_dag and a.operator!='cone':p.error('Shared DAG proposals require cone feedback')
    if a.critical_cover and (not a.shared_dag or not a.macro_input_isolation or a.cone_batch_from or a.seed_from):p.error('Critical cover requires isolated shared-DAG/macro-input feedback')
    if a.macro_input_isolation and not a.shared_dag:p.error('Macro input isolation requires shared DAG proposals')
    if a.reuse_clone_branches and not a.shared_dag:p.error('Clone branch transfer requires shared DAG proposals')
    if a.wide_mapping and (a.operator!='cone' or a.shared_dag or a.macro_input_isolation):p.error('Wide mapping uses its dedicated cone feedback/provider')
    if a.existing_wide_remap and (a.operator!='cone' or a.wide_mapping or a.shared_dag or a.batch_from or a.seed_from or a.cone_batch_from or a.wide_batch_from or a.diagnostic_from):p.error('Existing wide remapping requires its isolated fixed-topology cone feedback/provider')
    if a.general_wide_recompile and (a.operator!='cone' or a.existing_wide_remap or a.wide_mapping or a.shared_dag or a.batch_from or a.seed_from or a.cone_batch_from or a.wide_batch_from or a.diagnostic_from):p.error('General wide recompilation requires its isolated feedback/provider')
    if a.cross_macro_recompile and (a.operator!='cone' or a.general_wide_recompile or a.existing_wide_remap or a.wide_mapping or a.shared_dag or a.batch_from or a.seed_from or a.cone_batch_from or a.wide_batch_from or a.diagnostic_from):p.error('Cross-macro recompilation requires its isolated feedback/provider')
    if a.wide_trust_region and (a.operator!='cone' or a.cross_macro_recompile or a.general_wide_recompile or a.existing_wide_remap or a.wide_mapping or a.shared_dag or a.batch_from or a.seed_from or a.cone_batch_from or a.wide_batch_from or a.diagnostic_from):p.error('Wide trust-region search requires its isolated feedback/provider')
    if a.joint_wide_move_map and not a.wide_trust_region:p.error('Joint move/remapping requires wide trust-region feedback')
    if a.wide_group_move and not a.wide_trust_region:p.error('Group moves require wide trust-region feedback')
    if a.group_ordinary_luts and not a.wide_group_move:p.error('Ordinary group moves require wide-group-move')
    if a.trust_radius!=6 and not a.wide_trust_region:p.error('Expanded radius requires wide trust-region feedback')
    if a.global_frontier and not a.wide_mapping:p.error('Global frontier currently requires wide mapping; it does not add movable-state support')
    if a.wide_batch_from:
        a.wide_batch_from=a.wide_batch_from.resolve();wm=load_json(a.wide_batch_from/'manifest.json')
        if not a.wide_mapping or wm.get('status')!='routed' or not wm.get('wide_mapping') or wm.get('source_netlist_sha256')!=sha(a.placement/'packed.json') or wm.get('source_model_sha256')!=sha(a.model/'model.json'):p.error('Wide batch requires completed measured search from exact source/model')
    if a.seed_from:
        a.seed_from=a.seed_from.resolve()
        if a.operator!='cone' or a.batch_from or a.diagnostic_from:p.error('Warm seed is currently supported only by ordinary physical-cone search')
    if a.cone_batch_from:
        a.cone_batch_from=a.cone_batch_from.resolve();cm=load_json(a.cone_batch_from/'manifest.json')
        if a.operator!='cone' or cm.get('status')!='routed' or cm.get('source_netlist_sha256')!=sha(a.placement/'packed.json') or cm.get('source_model_sha256')!=sha(a.model/'model.json'):p.error('Cone batch requires completed search from this exact source/model')
    for n in ('placement','routes','wire','model','out'):setattr(a,n,getattr(a,n).resolve())
    if a.batch_from:
        a.batch_from=a.batch_from.resolve();bm=load_json(a.batch_from/'manifest.json')
        if a.rounds!=1 or bm.get('status')!='routed' or bm.get('source_netlist_sha256')!=sha(a.placement/'packed.json'):p.error('Batch requires one round and a completed search from this exact source')
    if a.diagnostic_from:
        a.diagnostic_from=a.diagnostic_from.resolve();dm=load_json(a.diagnostic_from/'manifest.json')
        if a.rounds!=1 or a.batch_from or a.diagnostic_index<0 or dm.get('source_netlist_sha256')!=sha(a.placement/'packed.json') or dm.get('source_placement_sha256')!=sha(a.placement/'placement.json') or dm.get('source_routes_sha256')!=sha(a.routes/'routes.json') or dm.get('source_model_sha256')!=sha(a.model/'model.json'):p.error('Diagnostic replay requires exact source/model binding and one round')
    if a.worker:return worker(a)
    from workspace_budget import reserve
    reserve(ROOT,350_000_000);prepare(a.out);sources=a.out/'sources';sources.mkdir()
    for n in ('ownpr_joint_lut_search.py','ownpr_lut_graph_feedback.py','ownpr_bidir_timing.py','ownpr_multilevel_cut.py','ownpr_cell_timing.py','ownpr_incremental_routes.py','ownpr_router.py','ownpr_alias_feedback.py','ownpr_critical_chain_move.py','ownpr_state_safety.py','ownpr_model_reference.py','ownpr_carry_feedback.py','ownpr_carry_translate.py','ownpr_path_cone.py','ownpr_shared_dag.py','ownpr_shared_feedback.py','ownpr_clone_route_seeds.py','ownpr_wide_mapping.py','ownpr_wide_mapping_feedback.py','ownpr_global_timing_frontier.py','audit_joint_boundary_coverage.py'):shutil.copyfile(ROOT/'scripts'/n,sources/n)
    if a.existing_wide_remap or a.general_wide_recompile or a.cross_macro_recompile or a.wide_trust_region:
        for n in ('ownpr_existing_wide_remap.py','reorder_ownpr_wide_inputs.py','prune_ownpr_lut_inputs.py'):shutil.copyfile(ROOT/'scripts'/n,sources/n)
    if a.general_wide_recompile or a.cross_macro_recompile or a.wide_trust_region:shutil.copyfile(ROOT/'scripts/ownpr_general_wide_recompile.py',sources/'ownpr_general_wide_recompile.py')
    if a.general_wide_recompile or a.cross_macro_recompile or a.wide_trust_region:shutil.copyfile(ROOT/'scripts/register_control_map.py',sources/'register_control_map.py')
    if a.cross_macro_recompile or a.wide_trust_region:shutil.copyfile(ROOT/'scripts/ownpr_cross_macro_recompile.py',sources/'ownpr_cross_macro_recompile.py')
    if a.wide_trust_region:shutil.copyfile(ROOT/'scripts/ownpr_wide_trust_region.py',sources/'ownpr_wide_trust_region.py')
    if a.critical_cover:shutil.copyfile(ROOT/'scripts/ownpr_critical_cover.py',sources/'ownpr_critical_cover.py')
    cmd=[str(ROOT/'binary_audit/flow/runtime/bin/python.exe'),str(Path(__file__).resolve()),'--worker','--seconds',str(a.seconds),'--trials',str(a.trials),'--rounds',str(a.rounds),'--per-round',str(a.per_round),'--critical-window',str(a.critical_window),'--operator',a.operator]
    for n in ('placement','routes','wire','model','out'):cmd+=['--'+n,str(getattr(a,n))]
    if a.batch_from:cmd+=['--batch-from',str(a.batch_from)]
    if a.seed_from:cmd+=['--seed-from',str(a.seed_from)]
    if a.shared_dag:cmd+=['--shared-dag']
    if a.critical_cover:cmd+=['--critical-cover']
    if a.macro_input_isolation:cmd+=['--macro-input-isolation']
    if a.reuse_clone_branches:cmd+=['--reuse-clone-branches']
    if a.wide_mapping:cmd+=['--wide-mapping']
    if a.existing_wide_remap:cmd+=['--existing-wide-remap']
    if a.general_wide_recompile:cmd+=['--general-wide-recompile']
    if a.cross_macro_recompile:cmd+=['--cross-macro-recompile']
    if a.wide_trust_region:cmd+=['--wide-trust-region']
    if a.joint_wide_move_map:cmd+=['--joint-wide-move-map']
    if a.wide_group_move:cmd+=['--wide-group-move']
    if a.group_ordinary_luts:cmd+=['--group-ordinary-luts']
    cmd+=['--trust-radius',str(a.trust_radius)]
    if a.global_frontier:cmd+=['--global-frontier']
    if a.wide_batch_from:cmd+=['--wide-batch-from',str(a.wide_batch_from)]
    if a.cone_batch_from:cmd+=['--cone-batch-from',str(a.cone_batch_from)]
    if a.diagnostic_from:cmd+=['--diagnostic-from',str(a.diagnostic_from),'--diagnostic-index',str(a.diagnostic_index)]
    with (a.out/'worker.log').open('w') as log:
        process=subprocess.Popen(cmd,cwd=ROOT,env=dict(os.environ,PYTHONHOME=sys.base_prefix),stdout=log,stderr=subprocess.STDOUT,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        try:code=process.wait(timeout=a.seconds+40)
        except subprocess.TimeoutExpired:
            from run_ownpr_pin_closure import stop_owned_process_tree
            stop_owned_process_tree(process);raise
    result=load_json(a.out/'manifest.json') if (a.out/'manifest.json').exists() else dict(status='worker_failed');result.update(returncode=code,source_sha256={f.name:sha(f) for f in sources.iterdir()});(a.out/'manifest.json').write_text(json.dumps(result,indent=2));print(json.dumps(result,indent=2))
    if code:raise SystemExit(code)


if __name__=='__main__':main()
