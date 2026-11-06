"""Critical LUT input permutation with truth-table proof and fixed package pins."""
from ownpr_image_storage import read_text as read_artifact_text
import argparse,copy,json,os,re,shutil,subprocess,sys,time
from pathlib import Path
from gowin_probe import ROOT,prepare
from run_abc_reproduction import sha


def permute(cell,a,b):
    cell=copy.deepcopy(cell);k=int(cell['type'][-1]);old=int(cell['parameters']['INIT'],2);value=0
    if not 0<=a<k or not 0<=b<k or a==b:raise ValueError('Invalid LUT input permutation')
    for address in range(1<<k):
        mapped=address
        if ((address>>a)^(address>>b))&1:mapped^=(1<<a)|(1<<b)
        value|=((old>>mapped)&1)<<address
    cell['parameters']['INIT']=format(value,f'0{1<<k}b')
    cell['connections']['I'+str(a)],cell['connections']['I'+str(b)]=cell['connections']['I'+str(b)],cell['connections']['I'+str(a)]
    return cell


def setup_pin_targets(report,max_paths=4,max_pins=16,relative_window_ns=None):
    """Skip output-only worst paths and inspect a bounded set of failing LUT paths."""
    section=re.search(r'^3\.3\.1 Setup Analysis Report\n(.*?)(?=^3\.|\Z)',report,re.M|re.S)
    if not section:raise ValueError('Missing setup path report')
    blocks=re.split(r'^\s*Path(\d+)\s*$',section[1],flags=re.M);targets=[];seen=set();paths=[]
    values=[float(x) for x in re.findall(r'^Slack\s*:\s*([-\d.]+)',section[1],re.M)]
    if relative_window_ns is not None and (not values or not 0<relative_window_ns<=5):raise ValueError('Invalid relative setup window')
    threshold=min(values)+relative_window_ns if relative_window_ns is not None else None
    for index in range(1,len(blocks),2):
        body=blocks[index+1];slack=re.search(r'^Slack\s*:\s*([-\d.]+)',body,re.M)
        if not slack:raise ValueError('Setup path has no slack')
        from ownpr_timing_numbers import negative_slack
        if threshold is None and not negative_slack(slack[1]) or threshold is not None and float(slack[1])>threshold:continue
        arrival=re.search(r'Data Arrival Path:\n(.*?)(?=\nData Required Path:)',body,re.S)
        if not arrival:raise ValueError('Setup path has no arrival trace')
        pins=re.findall(r'tNET\s+(RR|FF)\s+\d+\s+\S+\s+(own_i_\d+)/I([0-3])',arrival[1])
        if not pins:continue
        paths.append(dict(path=int(blocks[index]),slack_ns=float(slack[1]),pins=[[a,'I'+i] for _,a,i in pins]))
        for pin in pins:
            if pin not in seen and len(targets)<max_pins:seen.add(pin);targets.append(pin)
        if len(paths)>=max_paths or len(targets)>=max_pins:break
    return targets,paths


def worker(out,source,sta,placement,use_unused=False,candidate_rank=0,window=None,pin_feedback=None,batch_count=1):
    from native_device_session import NativeDeviceSession
    from ownpr_device import Device,RoutingGraph
    from ownpr_expand_routes import expand
    from ownpr_router import validate
    from ownpr_route_design import bind
    from ownpr_cell_timing import NativeCellTiming
    from ownpr_timing_graph import TimingRoutingGraph
    from ownpr_branch_reconnect import reconnect
    from native_boundary_cec import prove
    from ownpr_lut_lifting import lift,independent
    from ownpr_pin_slack import downstream_budgets
    start=time.monotonic();native=NativeDeviceSession(out);graph=RoutingGraph(Device(),ROOT/'results/native_rrg_001');library=NativeCellTiming()
    search=TimingRoutingGraph(graph.device,ROOT/'results/native_rrg_001',native)
    design=json.loads((placement/'packed.json').read_text());places=json.loads((placement/'placement.json').read_text());cells=design['modules']['top']['cells']
    routes=json.loads((source/'routes.json').read_text());nets=json.loads((source/'nets.json').read_text());byname={n['name']:n for n in nets}
    aliases=json.loads((sta/'name_map.json').read_text())['instances'];reverse={v:k for k,v in aliases.items()}
    meta=json.loads((sta/'manifest.json').read_text())
    if meta['routes_sha256']!=sha(source/'routes.json') or meta['packed_sha256']!=sha(placement/'packed.json'):raise ValueError('Stale pin optimization feedback')
    checked=json.loads((sta/'handoff_audit.json').read_text())
    if meta['status']!='imported' or not meta['sta_completed'] or meta.get('handoff_audit_sha256')!=sha(sta/'handoff_audit.json') or checked['source_sha256']['probe.tr']!=sha(sta/'probe.tr'):raise ValueError('Unverified final STA pin feedback')
    if meta['placement_sha256']!=sha(placement/'placement.json'):raise ValueError('Stale physical pin feedback')
    report=read_artifact_text(sta/'probe.tr');budget=downstream_budgets(design,json.loads((sta/'name_map.json').read_text()),report) if use_unused and not pin_feedback else None
    targets,target_paths=setup_pin_targets(report,relative_window_ns=window);groups={g['lut']:g for g in places['clusters'].values()}
    from ownpr_wide_ff_packing import configure_graph
    configure_graph(graph,design,places);configure_graph(search,design,places)
    pin_budgets=None;pin_audit=None
    if pin_feedback:
        from ownpr_pin_feedback import load as load_pins,physical_budgets
        from ownpr_image_storage import load_json
        from ownpr_wire_budget import ledger,WireDependency
        logical,pin_audit=load_pins(pin_feedback,sta);pin_budgets=physical_budgets(graph,load_json(sta/'sta_endpoint_audit.json'),logical);dependencies=WireDependency(design)
    hard=None
    if places.get('hard_clusters'):
        from ownpr_image_storage import load_json
        rm=load_json(source/'manifest.json');file=source/'hard_endpoint_audit.json'
        if rm.get('hard_endpoint_audit_sha256')!=sha(file):raise ValueError('Hard endpoint capture changed')
        hard=load_json(file)
    constants={'0':0,'1':1}
    for cell in cells.values():
        if cell['type'] in ('GND','VCC'):constants[next(iter(cell['connections'].values()))[0]]=int(cell['type']=='VCC')
    grounds=[c['connections']['G'][0] for c in cells.values() if c['type']=='GND']
    clock=None;clock_nodes=set()
    if (source/'clock_tree.json').exists():
        from ownpr_clock_partition import partition
        from ownpr_clock_graph import ClockGraph
        clock=json.loads((source/'clock_tree.json').read_text())
        clock_model=ClockGraph.for_tree(clock)
        if sha(source/'clock_tree.json')!=meta['clock_tree_sha256']:raise ValueError('Pin feedback has a different dedicated clock')
        for row in clock['parents']:
            r,c,i=row['node']
            if min(r,c)>=1 and i in graph.device.names:clock_nodes.add(graph.node(r-1,c-1,graph.device.names[i]))
    from ownpr_ioreg_macros import reserved_points
    for r,c,i in reserved_points(design,places):clock_nodes.add(graph.node(r-1,c-1,graph.device.names[i]))
    def binding(candidate):
        allnets,static=bind(candidate,places,graph,hard_bindings=hard)
        ordinary=partition(graph,allnets,clock,clock_model)[0] if clock else allnets
        return ordinary,allnets,static
    logical_sinks={}
    for cluster in places['clusters'].values():
        for pin in cells[cluster['lut']]['connections']:
            if pin.startswith('I'):
                node=graph.node(cluster['row'],cluster['column'],'ABCD'[int(pin[1:])]+str(cluster['slot']));logical_sinks[node]=(cluster['lut'],pin)
        if cluster['ff']:
            physical=graph.device.register_bindings_for(cells[cluster['ff']]['type'])[cluster['slot']]
            for pin in cells[cluster['ff']]['connections']:
                if pin not in ('Q','D'):logical_sinks[graph.node(cluster['row'],cluster['column'],physical[pin])]=(cluster['ff'],pin)
    for name,io in places['fixed_io'].items():
        if cells[name]['type']=='OBUF':
            r,c,i=io['native_ports']['in'];logical_sinks[graph.node(r-1,c-1,graph.device.names[i])]=(name,'I')
    if places.get('ioreg_clusters'):
        from ownpr_ioreg_macros import validate as validate_ioreg
        for record in validate_ioreg(design,places,graph.device)['records']:
            for pin in ('D','CE',record['control_pin']):
                r,c,i=record['pins'][pin];logical_sinks[graph.node(r-1,c-1,graph.device.names[i])]=(record['instance'],pin)
    if places.get('mux_clusters'):
        from ownpr_mux_architecture import MuxArchitecture
        mux_model=MuxArchitecture()
        for name,loc in places['mux_clusters'].items():
            r,c=loc['row'],loc['column'];physical=mux_model.pins(graph.device.grid[r][c],loc['level'],loc['slot'])
            logical_sinks[graph.node(r,c,physical['S0'])]=(name,'S0')
    if places.get('arithmetic_clusters'):
        from ownpr_alu_architecture import AluArchitecture
        alu_architecture=AluArchitecture()
    for name,loc in places.get('arithmetic_clusters',{}).items():
        physical=alu_architecture.pins(graph.device.grid[loc['row']][loc['column']],loc['slot'],loc['mode'])
        for pin in ('I0','I1','I3'):logical_sinks[graph.node(loc['row'],loc['column'],physical[pin])]=(name,pin)
    def measure(ns,rs):
        physical=expand(graph,ns,rs,native.fanouts);parents={tuple(p['node']):tuple(p['parent']) if p['parent'] else None for n in physical['nets'].values() for p in n['parents']};result={}
        with native.parent_tree(parents):
            cache={p:[native.delay(p,s) for s in (1,2)] for p in parents}
            for name,net in physical['nets'].items():
                for sink in net['sinks']:
                    result[name,sink['resource']]=[[sum(cache[tuple(p)][s][i] for p in sink['path_sink_to_source']) for i in range(4)] for s in (0,1)]
        return result
    candidates=[];attempts=[]
    for transition,alias,index in targets:
        name=reverse[alias]
        if name not in groups:continue
        oldpin=int(index);group=groups[name];r,c,slot=group['row'],group['column'],group['slot'];cell=cells[name]
        for newpin in range(4 if use_unused else int(cell['type'][-1])):
            if newpin==oldpin:continue
            oldwire='ABCD'[oldpin]+str(slot);newwire='ABCD'[newpin]+str(slot);oldnode=graph.node(r,c,oldwire);newnode=graph.node(r,c,newwire)
            lifted=lift(cell,grounds[0]) if newpin>=int(cell['type'][-1]) and grounds else copy.deepcopy(cell)
            if newpin>=int(lifted['type'][-1]):continue
            first='net_'+str(lifted['connections']['I'+str(oldpin)][0]);other=lifted['connections']['I'+str(newpin)][0]
            second='net_'+str(other);names=[first];moves=[(first,oldnode,newnode,newwire)]
            if first not in byname:continue
            if other in constants:
                if not use_unused or not independent(lifted,newpin):continue
            else:
                if second==first or second not in byname:continue
                names.append(second);moves.append((second,newnode,oldnode,oldwire))
            changed=copy.deepcopy(design);changed['modules']['top']['cells'][name]=permute(lifted,oldpin,newpin)
            newnets,_,_=binding(changed);newbyname={n['name']:n for n in newnets};newroutes=copy.deepcopy(routes);valid=True
            if set(newbyname)!=set(byname):raise ValueError('Pin lifting changed the ordinary net set')
            for net,before,after,wire in moves:
                record=newroutes['nets'][net];pips=[];replaced=0
                for pip in record['pips']:
                    pr,pc,a,b=pip
                    if graph.node(pr,pc,b)==before:
                        if (pr,pc)!=(r,c):valid=False;break
                        pip=[pr,pc,a,wire];replaced+=1
                        if not graph.check_pip(pip):valid=False;break
                    pips.append(pip)
                if not valid or replaced!=1:valid=False;break
                used={newbyname[net]['source']}
                for pr,pc,a,b in pips:used.update((graph.node(pr,pc,a),graph.node(pr,pc,b)))
                record.update(pips=pips,nodes=sorted(used),parent={},estimated_arrival_cost={})
            if not valid:
                # Input wires need not share a switch source. Reconnect each
                # affected terminal branch to the permuted physical endpoint.
                newroutes=copy.deepcopy(routes);valid=True
                owners={node:n for n,record in routes['nets'].items() if n not in names for node in record['nodes']}
                owners.update({n:'__dedicated_clock__' for n in clock_nodes})
                for net,before,after,_ in moves:
                    variants,_=reconnect(search,newbyname[net],routes['nets'][net],after,owners,start+150,
                        max_expansions=5000,max_candidates=5,labels_per_node=1,original_sink=before)
                    if not variants:valid=False;break
                    newroutes['nets'][net]=variants[0]
                    for node in variants[0]['nodes']:owners[node]=net
                if not valid:continue
            try:
                validate(graph,newnets,newroutes)
                if clock_nodes & {node for r in newroutes['nets'].values() for node in r['nodes']}:raise ValueError('LUT ECO overlaps a reserved clock or I/O output')
                baseline=measure([byname[n] for n in names],routes);observed=measure([newbyname[n] for n in names],newroutes)
            except ValueError:continue
            component=2 if transition=='RR' else 3
            before=baseline[names[0],oldnode][0][component]+library.lut_input(oldpin,slot,1)[component]
            after=observed[names[0],newnode][0][component]+library.lut_input(newpin,slot,1)[component]
            gain=before-after
            hold_guard=None
            if pin_budgets is not None:
                moved={(net,before):(after,oldpin if before==oldnode else newpin,newpin if after==newnode else oldpin) for net,before,after,_ in moves}
                deltas={}
                for net in names:
                    before_values={};after_values={}
                    for node in byname[net]['sinks']:
                        dest,op,np=moved.get((net,node),(node,None,None))
                        av=[*baseline[net,node][0][2:4],*baseline[net,node][1][:2]];bv=[*observed[net,dest][0][2:4],*observed[net,dest][1][:2]]
                        if op is not None:
                            ac=[*library.lut_input(op,slot,1)[2:4],*library.lut_input(op,slot,2)[:2]];bc=[*library.lut_input(np,slot,1)[2:4],*library.lut_input(np,slot,2)[:2]]
                            av=[x+y for x,y in zip(av,ac)];bv=[x+y for x,y in zip(bv,bc)]
                        before_values[node]=av;after_values[node]=bv
                    deltas[net]=(before_values,after_values)
                hold_guard=ledger(deltas,pin_budgets,dependencies.relations(deltas));hold_guard['scope']='Loaded wire changes plus moved input-cell arcs, charged to original receiver budgets; final STA mandatory';guard_ok=hold_guard['accepted']
            elif use_unused:
                shortening=[];margins=[];guard_ok=True
                moved={(net,before):(after,oldpin if before==oldnode else newpin,newpin if after==newnode else oldpin) for net,before,after,_ in moves}
                for net in names:
                    net_shortening=0.
                    for before_node in byname[net]['sinks']:
                        after_node,old_input,new_input=moved.get((net,before_node),(before_node,None,None))
                        old_min=list(baseline[net,before_node][1][:2]);new_min=list(observed[net,after_node][1][:2])
                        if old_input is not None:
                            old_min=[a+b for a,b in zip(old_min,library.lut_input(old_input,slot,2)[:2])];new_min=[a+b for a,b in zip(new_min,library.lut_input(new_input,slot,2)[:2])]
                        elif any(observed[net,after_node][0][i]>baseline[net,before_node][0][i]+.001 for i in (2,3)):guard_ok=False
                        delta=max(a-b for a,b in zip(old_min,new_min));net_shortening=max(net_shortening,delta)
                        if before_node not in logical_sinks:guard_ok=False;continue
                        margins.append(budget(*logical_sinks[before_node]))
                    shortening.append(max(0.,net_shortening))
                hold_guard=dict(maximum_path_shortening_ns=sum(shortening),minimum_downstream_slack_ns=min(margins) if margins else None,reserved_margin_ns=.010)
                if not margins or sum(shortening)>min(margins)-.010:guard_ok=False
            else:guard_ok=True
            witnesses=[p for p in target_paths if [alias,'I'+str(oldpin)] in p['pins']]
            info=dict(original_primitive=cell['type'],candidate_primitive=lifted['type'],hold_guard=hold_guard,guard_passed=guard_ok,instance=name,native_name=alias,old_input=oldpin,new_input=newpin,gain_ns=gain,old_wire_cell_ns=before,new_wire_cell_ns=after,nets=names,
                witness_paths=[p['path'] for p in witnesses],worst_path_slack_ns=min((p['slack_ns'] for p in witnesses),default=float('inf')))
            attempts.append(info)
            if gain>.001 and guard_ok:candidates.append((gain,changed,newnets,newroutes,info))
    (out/'pin_candidates.json').write_text(json.dumps(attempts,indent=2))
    if not candidates:raise ValueError('No improving legal pin permutation')
    candidates.sort(key=lambda r:-r[0])
    if candidate_rank>=len(candidates):raise ValueError('Requested pin candidate rank exceeds legal candidate count')
    selected=[];joint_budget=None
    if batch_count==1:
        gain,changed,newnets,newroutes,info=candidates[candidate_rank];selected=[info]
    else:
        from ownpr_wire_budget import path_total
        changed=copy.deepcopy(design);newroutes=copy.deepcopy(routes);used_nets=set();used_cells=set();covered=set();charges={};rejected=[]
        for gain,cd,cn,cr,info in sorted(candidates,key=lambda row:(row[4]['worst_path_slack_ns'],-row[0],row[4]['instance'])):
            name=info['instance'];ns=set(info['nets'])
            if name in used_cells or ns&used_nets or not set(info['witness_paths'])-covered:continue
            proposed=copy.deepcopy(changed);proposed['modules']['top']['cells'][name]=cd['modules']['top']['cells'][name]
            combined=copy.deepcopy(newroutes)
            for net in ns:combined['nets'][net]=cr['nets'][net]
            proposed_nets,_,_=binding(proposed)
            try:validate(graph,proposed_nets,combined)
            except ValueError as e:rejected.append(dict(instance=name,reason=str(e)));continue
            combined_charges={**charges,**{r['net']:r['charges'] for r in info['hold_guard']['nets']}};deps=dependencies.relations(combined_charges)
            totals={k:path_total(combined_charges,deps,k) for k in ('setup','hold')}
            if any(v is None or v>1.+1e-9 for v in totals.values()):rejected.append(dict(instance=name,reason='shared path budget exceeded',totals=totals));continue
            changed,newroutes,newnets=proposed,combined,proposed_nets;charges=combined_charges;used_nets|=ns;used_cells.add(name);covered.update(info['witness_paths']);selected.append(info)
            joint_budget=dict(accepted=True,normalized_loss_totals=totals,dependencies=deps,covered_paths=sorted(covered),full_design_sta_required=True)
            if len(selected)>=batch_count:break
        if not selected:raise ValueError('No jointly legal pin batch')
        (out/'batch_rejections.json').write_text(json.dumps(rejected,indent=2));info=selected[0]
    info=dict(info,candidate_rank=candidate_rank,legal_candidate_count=len(candidates));proof=prove(design,changed,out/'cec',timeout=60)
    newplace=out/'placement';newplace.mkdir();(newplace/'packed.json').write_text(json.dumps(changed,indent=2));shutil.copyfile(placement/'placement.json',newplace/'placement.json')
    (out/'routes.json').write_text(json.dumps(newroutes));(out/'nets.json').write_text(json.dumps(newnets,indent=2))
    _,allnets,static=binding(changed)
    if clock:
        (out/'all_nets.json').write_text(json.dumps(allnets,indent=2));shutil.copyfile(source/'clock_tree.json',out/'clock_tree.json')
    (out/'static_connections.json').write_text(json.dumps(static,indent=2))
    if hard:shutil.copyfile(source/'hard_endpoint_audit.json',out/'hard_endpoint_audit.json')
    pm=json.loads((placement/'manifest.json').read_text()) if (placement/'manifest.json').exists() else {}
    pm.update(status='placed',netlist_sha256=sha(newplace/'packed.json'),placement_sha256=sha(newplace/'placement.json'),proof=proof,pin_eco_source_sha256=sha(placement/'packed.json'))
    (newplace/'manifest.json').write_text(json.dumps(pm,indent=2))
    result=dict(status='routed',netlist_sha256=sha(newplace/'packed.json'),placement_sha256=sha(newplace/'placement.json'),vendor_pnr_executed=False,open_chip_database_used=False,
        net_count=len(newnets),pin_permutation=info,cec=proof,connectivity_audit=validate(graph,newnets,newroutes),wall_seconds=time.monotonic()-start,
        source_routes_sha256=sha(source/'routes.json'),feedback_report_sha256=sha(sta/'probe.tr'),target_setup_paths=target_paths,pin_permutations=selected,joint_delay_budget=joint_budget,timing_signoff=False,bitstream_generated=False)
    if clock:result['dedicated_clock']=dict(net=next(n['name'] for n in allnets if n.get('clock')),sinks=len(clock['paths_sink_to_source']),tree_sha256=sha(out/'clock_tree.json'))
    if hard:result['hard_endpoint_audit_sha256']=sha(out/'hard_endpoint_audit.json')
    result['pin_feedback']=pin_audit;result['relative_setup_window_ns']=window
    result['helper_sha256']={n:sha(ROOT/'scripts'/n) for n in ('ownpr_lut_lifting.py','ownpr_pin_slack.py','ownpr_route_design.py','ownpr_branch_reconnect.py','ownpr_clock_partition.py','ownpr_pin_feedback.py','ownpr_wire_budget.py','ownpr_cell_timing.py','ownpr_wide_ff_packing.py')}
    (out/'manifest.json').write_text(json.dumps(result,indent=2));print(json.dumps(result,indent=2),flush=True)


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--out',type=Path,required=True);ap.add_argument('--worker',action='store_true')
    ap.add_argument('--use-unused-inputs',action='store_true');ap.add_argument('--candidate-rank',type=int,default=0)
    ap.add_argument('--window',type=float);ap.add_argument('--pin-feedback',type=Path)
    ap.add_argument('--batch-count',type=int,default=1)
    ap.add_argument('--routes',type=Path,default=ROOT/'results/ownpr_int2float_eco_002');ap.add_argument('--sta',type=Path,default=ROOT/'results/native_ownpr_export_014');ap.add_argument('--placement',type=Path,default=ROOT/'results/ownpr_int2float_place_004');a=ap.parse_args()
    out=a.out.resolve();source=a.routes.resolve();sta=a.sta.resolve();placement=a.placement.resolve()
    if a.candidate_rank<0:ap.error('Candidate rank must be nonnegative')
    if not 1<=a.batch_count<=4 or a.batch_count>1 and (not a.pin_feedback or a.candidate_rank):ap.error('Batches need checked pin budgets, rank zero and size 1..4')
    if a.window is not None and not 0<a.window<=5:ap.error('Window must be 0..5 ns')
    if a.worker:return worker(out,source,sta,placement,a.use_unused_inputs,a.candidate_rank,a.window,a.pin_feedback,a.batch_count)
    from workspace_budget import reserve
    reserve(ROOT,300_000_000)
    prepare(out);shutil.copyfile(__file__,out/'eco_source.py.txt');python=ROOT/'binary_audit/flow/runtime/bin/python.exe'
    with (out/'worker.log').open('w') as log:
        cp=subprocess.run([str(python),str(Path(__file__).resolve()),'--worker','--out',str(out),'--routes',str(source),'--sta',str(sta),'--placement',str(placement),'--candidate-rank',str(a.candidate_rank),'--batch-count',str(a.batch_count)]+(['--use-unused-inputs'] if a.use_unused_inputs else [])+(['--window',str(a.window)] if a.window is not None else [])+(['--pin-feedback',str(a.pin_feedback.resolve())] if a.pin_feedback else []),cwd=python.parent,env=dict(os.environ,PYTHONHOME=sys.base_prefix),stdout=log,stderr=subprocess.STDOUT,timeout=180)
    if cp.returncode:
        (out/'manifest.json').write_text(json.dumps(dict(status='failed',returncode=cp.returncode,source_routes_sha256=sha(source/'routes.json'),
            feedback_report_sha256=sha(sta/'probe.tr'),vendor_pnr_executed=False,timing_signoff=False,bitstream_generated=False),indent=2))
        raise SystemExit('LUT pin ECO failed; see '+str(out/'worker.log'))
    print((out/'manifest.json').read_text())


if __name__=='__main__':main()
