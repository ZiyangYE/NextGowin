"""Reconcile one measured tree proposal with a completed hold repair.

All replaced nets need identical current physical terminals. The merged
design receives full configuration/STA checks within the original deadline.
"""
import argparse,json,math,re,shutil,subprocess,sys,time
from pathlib import Path
from datetime import datetime,timezone
from ownpr_device import ROOT,Device,RoutingGraph
from ownpr_image_storage import load_json,sha,read_text,copy_original,available
from ownpr_incremental_routes import signature
from ownpr_router import validate
from ownpr_wide_ff_packing import configure_graph
from run_ownpr_pin_closure import stop_owned_process_tree
from workspace_budget import reserve


def merge(graph,base_nets,base_routes,patch_nets,patch_routes,names):
    a={n['name']:n for n in base_nets};b={n['name']:n for n in patch_nets}
    if not names or len(names)!=len(set(names)):raise ValueError('Expected distinct proposed nets')
    for n in names:
        if n not in a or n not in b or signature(a[n])!=signature(b[n]):raise ValueError('Route patch physical terminals differ')
    result=dict(base_routes,nets=dict(base_routes['nets']))
    for n in names:result['nets'][n]=patch_routes['nets'][n]
    checked=validate(graph,base_nets,result)
    if not checked['connectivity_verified'] or not checked['no_node_conflicts']:raise ValueError('Merged proposal has conflicts')
    return result,checked


def main():
    p=argparse.ArgumentParser()
    for n in ('attempt','base-report','hold-state','patch','out'):p.add_argument('--'+n,type=Path,required=True)
    a=p.parse_args();original=load_json(a.attempt/'manifest.json');started=datetime.fromisoformat(original['started_utc']);budget=original['budget_seconds']
    if original['status']!='rejected' or budget>3600 or original['source_report_sha256']!=sha(a.base_report):raise ValueError('Original attempt changed')
    def remain():return budget-(datetime.now(timezone.utc)-started).total_seconds()
    if remain()<650:raise TimeoutError('Insufficient original deadline for merged export and qualification')
    reserve(ROOT,1_600_000_000);out=a.out.resolve();out.mkdir(exist_ok=False);shutil.copyfile(__file__,out/'runner_source.py.txt')
    hm=load_json(a.hold_state)
    if hm['status']!='bounded_hold_repair_complete' or hm['rounds'][-1]['decision']!='accepted_hard_constraints_repaired':raise ValueError('Hold repair not complete')
    state={k:Path(hm['selected'][k]) for k in ('placement','routes','wire','sta','clock_capture')};base=load_json(a.base_report)['designs'][0];original_row=load_json(a.attempt/'replay_checkpoint.json')['designs'][0]
    contract={k:ROOT/v for k,v in original_row['constraints'].items()};metrics=load_json(state['sta'].parent/'manifest.json')['metrics']
    pm=load_json(a.patch/'manifest.json');oldp=load_json(a.attempt/'candidate/placement.json');newp=load_json(state['placement']/'placement.json');proof=load_json(state['placement']/'manifest.json')
    if proof['source_netlist_sha256']!=sha(a.attempt/'candidate/packed.json') or proof['netlist_sha256']!=sha(state['placement']/'packed.json') or not all(r['cec']['equivalent'] for r in proof['proof']):raise ValueError('Hold repair proof chain changed')
    if any(newp['clusters'].get(n)!=row for n,row in oldp['clusters'].items()) or any(oldp[k]!=newp[k] for k in ('fixed_io','hard_clusters','arithmetic_clusters','mux_clusters')):raise ValueError('Hold repair changed existing physical sites')
    if pm['source_routes_sha256']!=sha(a.attempt/'implementation/route/routes.json') or pm['source_sta_sha256']!=sha(a.attempt/'implementation/export/native/probe.tr'):raise ValueError('Tree proposal source changed')
    if sha(state['routes']/'clock_tree.json')!=sha(a.patch/'clock_tree.json'):raise ValueError('Reconciled states use different clocks')
    if metrics['hold_violations'] or not metrics['other_checks_passed']:raise ValueError('Hold source not repaired')
    result=dict(status='running',original_started_utc=started.isoformat(),budget_seconds=budget,original_attempt=str(a.attempt),hold_state_sha256=sha(a.hold_state),patch_manifest_sha256=sha(a.patch/'manifest.json'),stages=[],accepted=False,goal_complete=False,vendor_pnr_executed=False)
    def save():
        result.update(total_elapsed_upper_bound_seconds=budget-remain(),metrics=metrics,selected={k:str(v) for k,v in state.items()});(out/'manifest.json').write_text(json.dumps(result,indent=2))
    def run(label,script,args):
        cmd=[sys.executable,str(ROOT/script),*map(str,args)];entry=dict(stage=label,status='running',command=cmd);result['stages'].append(entry);save();stamp=time.monotonic()
        if remain()<=0:raise TimeoutError('Original attempt deadline expired')
        with (out/(label+'.log')).open('w') as log:
            proc=subprocess.Popen(cmd,cwd=ROOT,stdout=log,stderr=subprocess.STDOUT,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            try:code=proc.wait(timeout=remain())
            except subprocess.TimeoutExpired:stop_owned_process_tree(proc);raise TimeoutError('Original attempt deadline expired')
        entry.update(status='complete' if code==0 else 'failed',returncode=code,wall_seconds=time.monotonic()-stamp);save()
        if code:raise RuntimeError('Merged refinement stage failed: '+label)
    def review():
        rel=lambda p:Path(p).resolve().relative_to(ROOT).as_posix()
        files=[state['placement']/'packed.json',state['placement']/'placement.json',*[state['routes']/n for n in ('routes.json','nets.json','all_nets.json','clock_tree.json')],*[state['sta']/n for n in ('manifest.json','probe.tr','handoff_audit.json')],state['wire']/'wire_delays.json']
        return dict(designs=[dict(design=base['design'],selected={k:rel(v) for k,v in state.items()},metrics=metrics,constraints={k:rel(v) for k,v in contract.items()},selected_source_sha256={rel(f):sha(f) for f in files})])
    save()
    try:
        graph=RoutingGraph(Device(),ROOT/'results/native_rrg_001');configure_graph(graph,load_json(state['placement']/'packed.json'),newp)
        names=[r['net'] for r in pm['critical_tree_rebuild']];merged,checked=merge(graph,load_json(state['routes']/'nets.json'),load_json(state['routes']/'routes.json'),load_json(a.patch/'nets.json'),load_json(a.patch/'routes.json'),names)
        routes=out/'route';routes.mkdir()
        files=['nets.json','all_nets.json','clock_tree.json','static_connections.json','hard_endpoint_audit.json','search_weights.json']
        for name in files:
            if available(state['routes']/name):copy_original(state['routes']/name,routes/name)
        for f in state['routes'].glob('*.txt'):shutil.copyfile(f,routes/f.name)
        (routes/'routes.json').write_text(json.dumps(merged));rm=load_json(state['routes']/'manifest.json')
        rm.update(status='routed',source_routes_sha256=sha(state['routes']/'routes.json'),source_sta_sha256=sha(state['sta']/'probe.tr'),connectivity_audit=checked,critical_tree_rebuild=pm['critical_tree_rebuild'],reconciled_patch_manifest_sha256=sha(a.patch/'manifest.json'),full_design_sta_required=True,timing_signoff=False)
        (routes/'manifest.json').write_text(json.dumps(rm,indent=2));result['merged_nets']=names
        run('export','scripts/export_ownpr.py',['--placement',state['placement'],'--routes',routes,'--clock-capture',state['clock_capture'],'--cst',contract['cst'],'--sdc',contract['sdc'],'--io-reference',contract['io_reference'],'--seconds',int(remain())-270,'--out',out/'export'])
        state=dict(state,routes=routes,wire=out/'export/wire',sta=out/'export/native');metrics=load_json(out/'export/manifest.json')['metrics'];result['target_metrics']=metrics
        (out/'replay_checkpoint.json').write_text(json.dumps(review(),indent=2));save()
        if not metrics['other_checks_passed'] or metrics['hold_violations']:raise ValueError('Merged candidate failed required hold/physical checks')
        period=float(re.search(r'-period\s+([\d.]+)',read_text(contract['sdc']))[1]);target=math.floor(min(metrics['reported_fmax_mhz']-.05,1000/(period-metrics['setup_wns_ns']+.02))*10)/10
        if remain()<250:raise TimeoutError('No original budget for full I/O frequency qualification')
        run('qualification','scripts/replay_coopt_checkpoint.py',['--case',base['design'],'--report',out/'replay_checkpoint.json','--target-mhz',target,'--seconds',int(remain())-1,'--out',out/'qualification'])
        qm=load_json(out/'qualification/manifest.json')['metrics'];result.update(qualified_mhz=target,qualified_metrics=qm)
        if qm['native_constraints_satisfied'] and qm['other_checks_passed'] and qm['setup_violations']==qm['hold_violations']==0:
            state=dict(state,sta=out/'qualification/export/native');metrics=qm;contract=dict(contract,sdc=out/'qualification/clock.sdc');(out/'qualified_replay_checkpoint.json').write_text(json.dumps(review(),indent=2))
            oldperiod=float(re.search(r'-period\s+([\d.]+)',read_text(ROOT/base['constraints']['sdc']))[1]);result['accepted']=target>1000/oldperiod+.01
        result['status']='accepted' if result['accepted'] else 'rejected';save()
    except Exception as e:
        result.update(status='timeout' if isinstance(e,(TimeoutError,subprocess.TimeoutExpired)) else 'failed',error=str(e));save();raise
    print(json.dumps({k:result.get(k) for k in ('status','qualified_mhz','total_elapsed_upper_bound_seconds','metrics')},indent=2))


if __name__=='__main__':main()
