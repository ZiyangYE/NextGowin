"""Bounded own clock search, calibrated encoding and complete fixed-IO STA.

Only internal clock paths may change. Data placement, nets, routes, source pad,
source SDC and all interface cycles remain frozen. Unchanged searches can reuse
the audited baseline, or explicitly re-export it to verify the complete chain.
"""
import argparse,json,shutil,subprocess,sys,time
from pathlib import Path
from ownpr_device import ROOT
from ownpr_place import sha
from workspace_budget import reserve
from run_ownpr_pin_closure import metric,improves,stop_owned_process_tree
from select_ownpr_candidate import check_contract
from audit_ownpr_native_export import audit as export_audit
from audit_ownpr_joint_routes import audit as joint_audit


def read(p):return json.loads(Path(p).read_text())


def clock_identity(tree):
    parents={tuple(r['node']):tuple(r['parent']) if r['parent'] is not None else None for r in tree['parents']}
    if len(parents)!=len(tree['parents']):raise ValueError('Duplicate clock parent')
    paths=sorted(tuple(tuple(n) for n in p) for p in tree['paths_sink_to_source'])
    if len(paths)!=len(set(paths)):raise ValueError('Duplicate clock endpoint path')
    return tuple(tree['source']),parents,paths


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('placement','routes','wire-timing','sta','clock-capture','cst','sdc','io-reference','out'):p.add_argument('--'+name,type=Path,required=True)
    p.add_argument('--seconds',type=int,default=600)
    p.add_argument('--verify-unchanged',action='store_true',help='Re-export the baseline if own search finds identical clock paths')
    p.add_argument('--slack-driven',action='store_true',help='Score shared clock-prefix changes with setup propagation and conservative hold bounds')
    p.add_argument('--slack-trials',type=int,default=256)
    a=p.parse_args()
    if not 1<=a.seconds<=3600:p.error('Complete attempt budget must be 1..3600 seconds')
    if not 1<=a.slack_trials<=2048:p.error('Clock feedback trial budget must be 1..2048')
    started=time.monotonic();deadline=started+a.seconds;storage=reserve(ROOT,700_000_000)
    out=a.out.resolve();out.mkdir(exist_ok=False);shutil.copyfile(__file__,out/'runner_source.py.txt')
    selected=dict(placement=str(a.placement.resolve()),routes=str(a.routes.resolve()),wire=str(a.wire_timing.resolve()),sta=str(a.sta.resolve()),clock_capture=str(a.clock_capture.resolve()))
    result=dict(status='running',initial=selected,selected=dict(selected),stages=[],evaluations=[],budget_seconds=a.seconds,storage_reservation=storage,
        vendor_placement_executed=False,vendor_routing_executed=False,open_chip_database_used=False,timing_signoff=False,full_bitstream_verified=False,
        runner_sha256=sha(out/'runner_source.py.txt'),scope='Supported dedicated clock paths only; full physical modes, multiple clocks and hardware acceptance remain incomplete.')
    def save():
        result['wall_seconds']=time.monotonic()-started;(out/'manifest.json').write_text(json.dumps(result,indent=2))
    def run(label,script,args,limit):
        reserve(ROOT,500_000_000);remaining=deadline-time.monotonic()
        if remaining<=0:raise TimeoutError('Complete clock closure deadline exhausted')
        command=[sys.executable,str(ROOT/script),*map(str,args)];row=dict(stage=label,status='running',command=command);result['stages'].append(row);save();stamp=time.monotonic()
        with (out/(label+'.log')).open('w') as log:
            process=subprocess.Popen(command,cwd=ROOT,stdout=log,stderr=subprocess.STDOUT,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            try:code=process.wait(timeout=min(remaining,limit))
            except subprocess.TimeoutExpired:
                stop_owned_process_tree(process);row.update(status='timeout',seconds=time.monotonic()-stamp);save();raise TimeoutError('Clock closure stage deadline: '+label)
        row.update(status='passed' if code==0 else 'failed',returncode=code,seconds=time.monotonic()-stamp);save()
        if code:raise RuntimeError('Clock closure stage failed: '+label)
    save()
    try:
        base=read(a.sta/'manifest.json');base_tree=read(a.routes/'clock_tree.json');best=metric(a.sta)
        if base['cst_sha256']!=sha(a.cst) or base['source_sdc_sha256']!=sha(a.sdc):raise ValueError('Clock closure source constraints changed')
        export_audit(a.sta,a.placement);joint_audit(a.placement,a.routes,a.wire_timing)
        if base['routes_sha256']!=sha(a.routes/'routes.json') or base['clock_tree_sha256']!=sha(a.routes/'clock_tree.json'):raise ValueError('Clock baseline differs from final STA')
        frozen=[a.placement/'packed.json',a.placement/'placement.json',*[a.routes/n for n in ('routes.json','nets.json','all_nets.json','static_connections.json','clock_tree.json')],a.cst,a.sdc]
        result.update(initial_metrics=best,metrics=best,source_sha256={str(f.resolve()):sha(f) for f in frozen});save()
        partial=out/'data_configuration.json';search=out/'search'
        feedback=[]
        if a.slack_driven:
            run('clock_feedback','scripts/ownpr_clock_slack.py',['--placement',a.placement.resolve(),'--sta',a.sta.resolve(),'--out',out/'clock_feedback.json'],60)
            feedback=['--slack-feedback',out/'clock_feedback.json','--slack-trials',a.slack_trials]
        result['slack_driven']=a.slack_driven
        run('data_configuration','scripts/ownpr_configuration.py',['--placement',a.placement.resolve(),'--routes',a.routes.resolve(),'--out',partial],60)
        run('search','experiments/run_ownpr_clock_timing_search.py',['--placement',a.placement.resolve(),'--routes',a.routes.resolve(),'--wire-timing',a.wire_timing.resolve(),
            '--baseline-clock',a.routes.resolve()/'clock_tree.json','--base-configuration',partial,*feedback,'--out',search],210)
        candidate_tree=read(search/'clock_tree.json');unchanged=clock_identity(base_tree)==clock_identity(candidate_tree)
        result.update(clock_paths_unchanged=unchanged,search_report_sha256=sha(search/'search_report.json'),verify_unchanged_requested=a.verify_unchanged);save()
        if not unchanged or a.verify_unchanged:
            clock_capture=a.clock_capture.resolve() if unchanged else out/'clock_encoding'
            treefile=a.routes.resolve()/'clock_tree.json' if unchanged else search/'clock_tree.json'
            if not unchanged:run('clock_encoding','experiments/native_clock_encoding_probe.py',['--tree',treefile,'--compact','--out',clock_capture],150)
            route=out/'route';export=out/'export'
            run('apply_clock','scripts/apply_ownpr_clock_tree.py',['--placement',a.placement.resolve(),'--routes',a.routes.resolve(),'--clock-tree',treefile,'--out',route],60)
            for n in ('routes.json','nets.json','all_nets.json','static_connections.json'):
                if sha(route/n)!=sha(a.routes/n):raise ValueError('Clock closure changed frozen data routing')
            remaining=int(deadline-time.monotonic())
            if remaining<=0:raise TimeoutError('No budget for full STA')
            run('export','scripts/export_ownpr.py',['--placement',a.placement.resolve(),'--routes',route,'--clock-capture',clock_capture,
                '--cst',a.cst.resolve(),'--sdc',a.sdc.resolve(),'--io-reference',a.io_reference.resolve(),'--seconds',remaining,'--out',export],remaining)
            sta=export/'native';wire=export/'wire'
            check_contract(base,read(sta/'manifest.json'),base_tree,read(sta/'clock_tree.json'),True)
            export_audit(sta,a.placement);joint_audit(a.placement,route,wire);observed=metric(sta)
            state=dict(placement=str(a.placement.resolve()),routes=str(route),wire=str(wire),sta=str(sta),clock_capture=str(clock_capture))
            accepted=improves(best,observed)
            if unchanged and {k:v for k,v in observed.items() if k!='handoff_sha256'}!={k:v for k,v in best.items() if k!='handoff_sha256'}:raise ValueError('Identical clock/data implementation changed final timing')
            result['evaluations'].append(dict(**state,metrics=observed,decision='accepted' if accepted else 'unchanged_verified' if unchanged else 'rejected_global_timing'))
            if accepted:result.update(selected=state,metrics=observed)
        if any(sha(Path(f))!=digest for f,digest in result['source_sha256'].items()):raise ValueError('Frozen clock closure input changed')
        result['status']='bounded_clock_attempt_complete';save()
    except Exception as error:
        result.update(status='timeout' if isinstance(error,TimeoutError) else 'failed',error=str(error));save();raise
    print(json.dumps(dict(status=result['status'],wall_seconds=result['wall_seconds'],clock_paths_unchanged=unchanged,metrics=result['metrics']),indent=2))


if __name__=='__main__':main()
