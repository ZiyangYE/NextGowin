"""Bounded synthesis/packing/incremental-routing loop with full native STA.

Starts from an audited own implementation. Supported candidates include wide
cuts, Shannon input reordering, exclusive macro moves, whole-net routing and
fixed-pad output-register packing. This is not the RTL front end or a complete
hard-resource/multi-clock backend.
"""
import argparse,json,shutil,subprocess,sys,time
from pathlib import Path
from collections import Counter
from ownpr_device import ROOT
from ownpr_place import sha
from ownpr_joint_feedback import plan
from run_ownpr_pin_closure import metric,improves,stop_owned_process_tree
from select_ownpr_candidate import check_contract
from audit_ownpr_native_export import audit as export_audit
from audit_ownpr_joint_routes import audit as joint_audit
from workspace_budget import reserve


def read(p):return json.loads(Path(p).read_text())
def counts(folder):return dict(Counter(c['type'] for c in read(Path(folder)/'packed.json')['modules']['top']['cells'].values()))
def lut_count(folder):return sum(v for k,v in counts(folder).items() if k.startswith('LUT'))


def check_unchanged_placement(sta,placement):
    meta=read(Path(sta)/'manifest.json');placement=Path(placement)
    if sha(placement/'packed.json')!=meta['packed_sha256'] or sha(placement/'placement.json')!=meta['placement_sha256']:
        raise ValueError('Route-only ECO changed the logical netlist or placement')

def completed_rejections(folder,base,base_tree):
    """Reuse only complete, freshly audited rejected trials; never an interrupted one."""
    folder=Path(folder);history=read(folder/'manifest.json');tried=set();records=[]
    for entry in history['rounds']:
        if entry['status']!='rejected_global_timing':continue
        source=entry['source'];sta=Path(source['sta']);before=metric(sta);meta=read(sta/'manifest.json')
        source_tree=read(sta/'clock_tree.json');check_contract(base,meta,base_tree,source_tree,True)
        if before!=entry['before']:raise ValueError('Historical trial baseline changed')
        feedback_file=Path(entry['folder'])/'feedback.json';feedback=read(feedback_file)
        if sha(feedback_file)!=entry['feedback_sha256'] or entry['proposal'] not in feedback['candidates']:raise ValueError('Historical synthesis proposal changed')
        for name,digest in feedback['source_sha256'].items():
            if sha(Path(name))!=digest:raise ValueError('Historical physical feedback input changed')
        if not entry['evaluations']:raise ValueError('Historical rejection has no final STA')
        for evaluation in entry['evaluations']:
            observed=metric(evaluation['sta'])
            if observed!=evaluation['metrics'] or evaluation['decision']!='rejected_global_timing' or improves(before,observed):raise ValueError('Historical rejection is inconsistent with final timing')
            check_contract(meta,read(Path(evaluation['sta'])/'manifest.json'),source_tree,read(Path(evaluation['sta'])/'clock_tree.json'),entry['proposal']['kind']=='output_ioreg')
            export_audit(evaluation['sta'],evaluation['placement']);joint_audit(evaluation['placement'],evaluation['routes'],evaluation['wire'])
        key=(sha(sta/'handoff_audit.json'),entry['proposal']['key']);tried.add(key)
        records.append(dict(state=key[0],proposal=key[1],history_manifest_sha256=sha(folder/'manifest.json'),round=entry['round']))
    return tried,records


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('placement','routes','wire-timing','sta','clock-capture','cst','sdc','io-reference','out'):p.add_argument('--'+name,type=Path,required=True)
    p.add_argument('--rounds',type=int,default=3);p.add_argument('--seconds',type=int,default=900);p.add_argument('--max-lut-growth',type=int,default=8)
    p.add_argument('--route-only',action='store_true',help='Evaluate routing ECO without a preceding synthesis rewrite')
    p.add_argument('--eco-setup-paths',type=int,default=1,help='Number of failing paths considered by routing ECO (1..100)')
    p.add_argument('--eco-whole-nets',type=int,default=0,help='Rebuild up to 4 critical ordinary trees in each ECO')
    p.add_argument('--wide-moves',action='store_true',help='Include calibrated exclusive wide-macro placement candidates')
    p.add_argument('--move-only',action='store_true',help='Evaluate only wide-macro moves, retaining the logical netlist')
    p.add_argument('--prune-only',action='store_true',help='Evaluate only CEC-proved removal of unused LUT input connections')
    p.add_argument('--arrival-reorder-only',action='store_true',help='Rank wide-input orders by propagated competing arrivals and sampled wires')
    p.add_argument('--selector-only',action='store_true',help='Evaluate small functions on dedicated MUX selector stages')
    p.add_argument('--reuse-branches',action='store_true',help='Preserve safe common branches when net endpoints change')
    p.add_argument('--no-route-eco',action='store_true',help='Skip the post-route ECO for a controlled initial-routing comparison')
    p.add_argument('--previous-attempt',type=Path,action='append',default=[])
    a=p.parse_args()
    if not 1<=a.seconds<=3600 or not 1<=a.rounds<=20 or a.max_lut_growth<0:p.error('Require 1..20 rounds, positive <=3600 second budget and nonnegative LUT growth')
    if not 1<=a.eco_setup_paths<=100:p.error('--eco-setup-paths must be 1..100')
    if not 0<=a.eco_whole_nets<=4:p.error('--eco-whole-nets must be 0..4')
    if sum((a.move_only,a.route_only,a.prune_only,a.arrival_reorder_only,a.selector_only))>1:p.error('The exclusive candidate modes are mutually exclusive')
    start=time.monotonic();deadline=start+a.seconds
    storage=reserve(ROOT,(a.rounds+1)*500_000_000)
    out=a.out.resolve();out.mkdir(exist_ok=False);shutil.copyfile(__file__,out/'runner_source.py.txt')
    selected=dict(placement=str(a.placement.resolve()),routes=str(a.routes.resolve()),wire=str(a.wire_timing.resolve()),sta=str(a.sta.resolve()),clock_capture=str(a.clock_capture.resolve()))
    best=metric(a.sta);base=read(a.sta/'manifest.json');base_tree=read(a.sta/'clock_tree.json');limit=lut_count(a.placement)+a.max_lut_growth
    if base['cst_sha256']!=sha(a.cst) or base['source_sdc_sha256']!=sha(a.sdc):raise ValueError('Joint closure changed the baseline constraint contract')
    export_audit(a.sta,a.placement);joint_audit(a.placement,a.routes,a.wire_timing)
    result=dict(status='running',initial=dict(selected),selected=dict(selected),initial_metrics=best,metrics=best,rounds=[],
        budget_seconds=a.seconds,maximum_attempts=a.rounds,maximum_lut_count=limit,runner_sha256=sha(out/'runner_source.py.txt'),
        source_cst_sha256=sha(a.cst),source_sdc_sha256=sha(a.sdc),vendor_placement_executed=False,vendor_routing_executed=False,
        open_chip_database_used=False,timing_signoff=False,full_bitstream_verified=False,
        scope='Automatic supported netlist rewrites, exclusive output-register packing and own incremental P&R; not a complete RTL or hard-resource flow.')
    result.update(route_only=a.route_only,eco_setup_paths=a.eco_setup_paths,eco_whole_nets=a.eco_whole_nets)
    result.update(wide_moves=a.wide_moves or a.move_only,move_only=a.move_only)
    result['prune_only']=a.prune_only
    result['arrival_reorder_only']=a.arrival_reorder_only
    result['selector_only']=a.selector_only
    result.update(reuse_branches=a.reuse_branches,route_eco_enabled=not a.no_route_eco)
    tried=set();result['storage_reservation']=storage;result['reused_completed_rejections']=[]
    for history in a.previous_attempt:
        keys,records=completed_rejections(history,base,base_tree);tried.update(keys);result['reused_completed_rejections'].extend(records)
    def save():
        result['wall_seconds']=time.monotonic()-start;(out/'manifest.json').write_text(json.dumps(result,indent=2))
    def run(entry,label,script,args,limit_seconds):
        reserve(ROOT,500_000_000)
        remaining=deadline-time.monotonic()
        if remaining<=0:raise TimeoutError('Complete joint attempt deadline exhausted')
        command=[sys.executable,str(ROOT/script),*map(str,args)];row=dict(stage=label,command=command,status='running');entry['stages'].append(row);save();stamp=time.monotonic()
        with (Path(entry['folder'])/(label+'.log')).open('w') as log:
            process=subprocess.Popen(command,cwd=ROOT,stdout=log,stderr=subprocess.STDOUT,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            try:code=process.wait(timeout=min(remaining,limit_seconds))
            except subprocess.TimeoutExpired:
                stop_owned_process_tree(process);row.update(status='timeout',seconds=time.monotonic()-stamp);save();raise TimeoutError('Stage deadline: '+label)
        row.update(status='passed' if code==0 else 'failed',returncode=code,seconds=time.monotonic()-stamp);save()
        if code:raise RuntimeError('Joint stage failed: '+label)
    def evaluate(entry,label,placement,routes):
        folder=Path(entry['folder'])/label
        wire=folder/'wire';wire_args=[]
        if a.arrival_reorder_only:
            from ownpr_reorder_prediction import assess_routed
            wire=Path(entry['folder'])/(label+'_wire')
            run(entry,label+'_wire','scripts/measure_ownpr_wire_delays.py',['--routes',routes,'--out',wire],min(120,max(1,deadline-time.monotonic()-70)))
            prediction=assess_routed(entry['source']['placement'],entry['source']['sta'],placement,routes,wire,entry['proposal']['root'])
            path=Path(entry['folder'])/(label+'_arrival_prediction.json');path.write_text(json.dumps(prediction,indent=2))
            entry.setdefault('routed_arrival_predictions',[]).append(dict(stage=label,path=str(path),sha256=sha(path),metrics=prediction['metrics']))
            wire_args=['--wire-timing',wire];save()
        budget=min(180,int(deadline-time.monotonic()))
        if budget<=0:raise TimeoutError('No remaining budget for complete export')
        run(entry,label,'scripts/export_ownpr.py',['--placement',placement,'--routes',routes,'--clock-capture',entry['source']['clock_capture'],
            '--cst',a.cst.resolve(),'--sdc',a.sdc.resolve(),'--io-reference',a.io_reference.resolve(),*wire_args,'--seconds',budget,'--out',folder],budget)
        sta=folder/'native';parent=Path(entry['source']['sta'])
        check_contract(read(parent/'manifest.json'),read(sta/'manifest.json'),read(parent/'clock_tree.json'),read(sta/'clock_tree.json'))
        check_contract(base,read(sta/'manifest.json'),base_tree,read(sta/'clock_tree.json'),True)
        observed=metric(sta);state=dict(placement=str(placement),routes=str(routes),wire=str(wire),sta=str(sta),clock_capture=entry['source']['clock_capture'])
        accepted=improves(result['metrics'],observed)
        entry['evaluations'].append(dict(**state,metrics=observed,decision='accepted' if accepted else 'rejected_global_timing'))
        if accepted:result.update(metrics=observed,selected=state)
        save();return sta,observed
    save()
    for index in range(1,a.rounds+1):
        if deadline-time.monotonic()<150:result['status']='budget_exhausted_before_next_candidate';break
        selected=result['selected'];placement=Path(selected['placement']);routes=Path(selected['routes']);sta=Path(selected['sta'])
        feedback=plan(placement,routes,sta,max_candidates=200 if a.move_only or a.prune_only or a.arrival_reorder_only or a.selector_only else 24,max_growth=max(0,limit-lut_count(placement)),include_moves=a.wide_moves or a.move_only,include_prune=not(a.move_only or a.route_only or a.arrival_reorder_only or a.selector_only),include_selectors=a.selector_only)
        if a.selector_only:feedback['candidates']=[p for p in feedback['candidates'] if p['kind']=='selector_cascade']
        if a.arrival_reorder_only:
            from ownpr_reorder_prediction import rank
            feedback['candidates'],feedback['arrival_projection']=rank(placement,sta,feedback['candidates'],seconds=min(60,max(1,deadline-time.monotonic()-150)))
            feedback['source_sha256'].update(feedback['arrival_projection']['source_sha256'])
            feedback['ranking']='Propagated reported-endpoint WNS, violations and TNS with maximum-observed wire transfer; prediction only'
        if a.prune_only:feedback['candidates']=[p for p in feedback['candidates'] if p['kind']=='lut_input_prune']
        if a.move_only:
            from ownpr_wide_macro_move import diversify
            feedback['candidates']=diversify([p for p in feedback['candidates'] if p['kind']=='wide_move'])[:24]
            feedback['ranking']='Worst path slack, distinct macro before neighboring sites, heuristic wire gain; final STA required'
        if a.route_only:
            feedback['candidates']=([dict(kind='route_eco',key=f'route_eco:paths{a.eco_setup_paths}'+(f':whole{a.eco_whole_nets}' if a.eco_whole_nets else ''),lut_growth=0,setup_paths=a.eco_setup_paths,whole_nets=a.eco_whole_nets)]
                if feedback['negative_setup_paths'] else [])
        state=sha(sta/'handoff_audit.json');available=[c for c in feedback['candidates'] if (state,c['key']) not in tried]
        if not available:result['status']='supported_candidates_exhausted';break
        proposal=available[0];tried.add((state,proposal['key']));rd=out/f'round_{index:03d}';rd.mkdir();(rd/'feedback.json').write_text(json.dumps(feedback,indent=2))
        entry=dict(round=index,folder=str(rd),status='running',proposal=proposal,source=dict(selected),before=result['metrics'],feedback_sha256=sha(rd/'feedback.json'),stages=[],evaluations=[])
        result['rounds'].append(entry);save();newplace=rd/'placement';newroute=rd/'route'
        try:
            if proposal['kind']=='route_eco':
                eco=rd/'eco';budget=min(270,int(deadline-time.monotonic())-65)
                if budget<1:raise TimeoutError('Insufficient remaining budget for route ECO and full export')
                run(entry,'route_eco','scripts/ownpr_route_eco.py',['--placement',placement,'--routes',routes,'--sta',sta,
                    '--setup-paths',a.eco_setup_paths,'--whole-net-count',a.eco_whole_nets,'--out',eco],budget)
                check_unchanged_placement(sta,placement)
                entry['proof']=[];entry['logical_netlist_byte_identical']=True
                evaluate(entry,'eco_export',placement,eco)
                entry['status']='accepted' if entry['evaluations'][-1]['decision']=='accepted' else 'rejected_global_timing';save()
                if result['metrics']['native_constraints_satisfied']:result['status']='supported_native_constraints_satisfied';break
                continue
            elif proposal['kind']=='output_ioreg':
                nested=rd/'ioreg';budget=min(600,int(deadline-time.monotonic())-10)
                if budget<150:raise TimeoutError('Insufficient remaining budget for complete I/O packing')
                run(entry,'ioreg_closure','scripts/run_ownpr_ioreg_closure.py',['--placement',placement,'--routes',routes,'--sta',sta,
                    '--cst',a.cst.resolve(),'--sdc',a.sdc.resolve(),'--io-reference',a.io_reference.resolve(),'--port',proposal['port'],
                    '--incremental','--seconds',budget,'--out',nested],budget+5)
                child=read(nested/'manifest.json')
                if child['status']!='bounded_ioreg_attempt_complete' or not child['logical_netlist_byte_identical']:raise ValueError('Incomplete I/O packing attempt')
                entry['proof']=[];entry['logical_netlist_byte_identical']=True
                for evaluation in child['candidates']:
                    cp,cr,cw,cs=(Path(evaluation[k]) for k in ('placement','routes','wire','sta'))
                    if sha(cp/'packed.json')!=sha(placement/'packed.json') or read(cp/'placement.json')['fixed_io']!=read(placement/'placement.json')['fixed_io']:raise ValueError('I/O packing changed logical/interface contract')
                    check_contract(base,read(cs/'manifest.json'),base_tree,read(cs/'clock_tree.json'),True)
                    export_audit(cs,cp);joint_audit(cp,cr,cw);observed=metric(cs)
                    if observed!=evaluation['metrics']:raise ValueError('Nested I/O timing changed')
                    accepted=improves(result['metrics'],observed)
                    state=dict(placement=str(cp),routes=str(cr),wire=str(cw),sta=str(cs),clock_capture=str(nested/'clock_encoding'))
                    entry['evaluations'].append(dict(**state,metrics=observed,decision='accepted' if accepted else 'rejected_global_timing'))
                    if accepted:result.update(metrics=observed,selected=state)
                entry['primitive_counts']=counts(result['selected']['placement'])
                entry['status']='accepted' if any(e['decision']=='accepted' for e in entry['evaluations']) else 'rejected_global_timing';save()
                if result['metrics']['native_constraints_satisfied']:result['status']='supported_native_constraints_satisfied';break
                continue
            elif proposal['kind']=='lut_input_prune':
                run(entry,'prune_inputs','scripts/prune_ownpr_lut_inputs.py',['--base',placement,'--out',newplace],120)
                if sha(newplace/'placement.json')!=sha(placement/'placement.json'):raise ValueError('LUT pruning changed physical placement')
            elif proposal['kind']=='wide_move':
                run(entry,'move_macro','scripts/ownpr_wide_macro_move.py',['--base',placement,'--root',proposal['root'],
                    '--row',proposal['row'],'--column',proposal['column'],'--out',newplace],60)
                if sha(newplace/'packed.json')!=sha(placement/'packed.json'):raise ValueError('Macro placement changed logical netlist')
                entry['logical_netlist_byte_identical']=True
            elif proposal['kind']=='wide_reorder':
                run(entry,'rewrite','scripts/reorder_ownpr_wide_inputs.py',['--base',placement,'--root',proposal['root'],'--late-bit',proposal['late_bit'],
                    *(['--target-index',proposal['target_index']] if 'target_index' in proposal else []),'--out',newplace],120)
            elif proposal['kind']=='wide_cut':
                cone=rd/'cone';run(entry,'rewrite','scripts/ownpr_wide_cone.py',['--netlist',placement/'packed.json','--parent',proposal['parent'],'--root',proposal['root'],'--out',cone],120)
                run(entry,'pack_place','scripts/prepare_ownpr_wide_placement.py',['--base',placement,'--cone',cone,'--out',newplace],60)
            elif proposal['kind']=='selector_cascade':
                cone=rd/'cone';run(entry,'rewrite','scripts/ownpr_selector_cascade.py',['--netlist',placement/'packed.json','--root',proposal['root'],'--late-bit',proposal['late_bit'],*(['--selector-count',proposal['selector_count']] if 'selector_count' in proposal else []),'--out',cone],120)
                run(entry,'pack_place','scripts/prepare_ownpr_wide_placement.py',['--base',placement,'--cone',cone,'--out',newplace],60)
            else:raise ValueError('Unsupported automatic synthesis candidate')
            proof=read(newplace/'manifest.json')['proof']
            if proposal['kind']!='wide_move' and (not proof or not all(r['cec']['equivalent'] for r in proof)):raise ValueError('Unproved synthesis rewrite')
            if lut_count(newplace)>limit or read(newplace/'placement.json')['fixed_io']!=read(placement/'placement.json')['fixed_io']:raise ValueError('Rewrite changed fixed IO or exceeded area budget')
            entry['proof']=proof;entry['primitive_counts']=counts(newplace)
            route_budget=min(300,int(deadline-time.monotonic())-80)
            if route_budget<1:raise TimeoutError('Insufficient remaining budget for routing and final export')
            run(entry,'route','scripts/run_ownpr_timing_route.py',['--placement',newplace,'--clock-tree',routes/'clock_tree.json','--reuse-routes',routes,
                *(['--reuse-branches'] if a.reuse_branches else []),'--timeout',route_budget,'--out',newroute],route_budget+20)
            entry['incremental_routing']=read(newroute/'manifest.json').get('incremental')
            candidate_sta,observed=evaluate(entry,'initial_export',newplace,newroute)
            if not a.no_route_eco and (observed['hold_violations'] or observed['setup_violations']) and deadline-time.monotonic()>100:
                eco=rd/'eco';run(entry,'route_eco','scripts/ownpr_route_eco.py',['--placement',newplace,'--routes',newroute,'--sta',candidate_sta,'--setup-paths',a.eco_setup_paths,'--whole-net-count',a.eco_whole_nets,'--out',eco],min(270,max(1,deadline-time.monotonic()-65)))
                evaluate(entry,'eco_export',newplace,eco)
            entry['status']='accepted' if any(e['decision']=='accepted' for e in entry['evaluations']) else 'rejected_global_timing';save()
            if result['metrics']['native_constraints_satisfied']:result['status']='supported_native_constraints_satisfied';break
        except Exception as error:
            entry.update(status='timeout' if isinstance(error,TimeoutError) else 'failed',error=str(error));save()
            if isinstance(error,TimeoutError):result['status']='attempt_timeout_with_retained_best';break
    else:result['status']='bounded_rounds_complete'
    result['selected_primitive_counts']=counts(result['selected']['placement']);save()
    print(json.dumps(dict(status=result['status'],wall_seconds=result['wall_seconds'],selected=result['selected'],metrics=result['metrics']),indent=2))


if __name__=='__main__':main()
