"""Bounded fixed-pad output-register packing, own routing and native export.

The entire attempt shares one wall deadline. Native P&R is never called;
the native encoder calibrates the new clock tree and exports our final graph.
"""
import argparse,json,shutil,subprocess,sys,time
from pathlib import Path
from ownpr_device import ROOT
from ownpr_place import sha
from run_ownpr_pin_closure import metric,improves,stop_owned_process_tree
from select_ownpr_candidate import check_contract
from workspace_budget import reserve


def read(p):return json.loads(Path(p).read_text())


def bind_verified_constraints(placement, baseline):
    """Carry the already hash-verified STA contract into a derived placement."""
    path=placement/'manifest.json';meta=read(path)
    for key,source in [('cst_sha256','cst_sha256'),('sdc_sha256','source_sdc_sha256')]:
        expected=baseline[source]
        if key in meta and meta[key]!=expected:
            raise ValueError('Derived placement constraint differs from verified baseline: '+key)
        meta[key]=expected
    meta['constraint_provenance']='Verified source native STA; identical source netlist, placement, routes and constraint hashes checked by runner'
    path.write_text(json.dumps(meta,indent=2))


def main():
    p=argparse.ArgumentParser();p.add_argument('--placement',type=Path,required=True);p.add_argument('--routes',type=Path,required=True);p.add_argument('--sta',type=Path,required=True)
    p.add_argument('--cst',type=Path,required=True);p.add_argument('--sdc',type=Path,required=True);p.add_argument('--io-reference',type=Path,required=True)
    p.add_argument('--port',action='append',required=True);p.add_argument('--out',type=Path,required=True);p.add_argument('--seconds',type=int,default=900);p.add_argument('--incremental',action='store_true')
    p.add_argument('--cone-depth',type=int,default=0);p.add_argument('--cone-fraction',type=float,default=.6);a=p.parse_args()
    if not 1<=a.seconds<=3600:p.error('The complete attempt requires a deadline of at most one hour')
    start=time.monotonic();deadline=start+a.seconds;storage=reserve(ROOT,1_000_000_000)
    out=a.out.resolve();out.mkdir(exist_ok=False);shutil.copyfile(__file__,out/'runner_source.py.txt')
    base=read(a.sta/'manifest.json');base_route=read(a.routes/'manifest.json');best=metric(a.sta);base_tree=read(a.sta/'clock_tree.json')
    if (base['packed_sha256']!=sha(a.placement/'packed.json') or base['placement_sha256']!=sha(a.placement/'placement.json') or base['routes_sha256']!=sha(a.routes/'routes.json')
        or base['cst_sha256']!=sha(a.cst) or base['source_sdc_sha256']!=sha(a.sdc) or base_route['status']!='routed' or base_route['vendor_pnr_executed']):raise ValueError('Baseline or frozen interface contract changed')
    result=dict(status='running',budget_seconds=a.seconds,storage_reservation=storage,incremental_routing_requested=a.incremental,stages=[],candidates=[],initial_metrics=best,metrics=best,
        selected=dict(placement=str(a.placement.resolve()),routes=str(a.routes.resolve()),sta=str(a.sta.resolve())),
        ports=a.port,source_netlist_sha256=base['packed_sha256'],source_placement_sha256=base['placement_sha256'],
        source_cst_sha256=sha(a.cst),source_sdc_sha256=sha(a.sdc),runner_sha256=sha(out/'runner_source.py.txt'),
        vendor_placement_executed=False,vendor_routing_executed=False,open_chip_database_used=False,timing_signoff=False,full_bitstream_verified=False)
    def save():
        result['wall_seconds']=time.monotonic()-start;(out/'manifest.json').write_text(json.dumps(result,indent=2))
    def run(label,script,args,limit):
        reserve(ROOT,500_000_000)
        remaining=deadline-time.monotonic()
        if remaining<=1:raise TimeoutError('Complete I/O-register attempt budget exhausted')
        command=[sys.executable,str(ROOT/script),*map(str,args)];stamp=time.monotonic();record=dict(stage=label,command=command,status='running');result['stages'].append(record);save()
        with (out/(label+'.log')).open('w') as log:
            process=subprocess.Popen(command,cwd=ROOT,stdout=log,stderr=subprocess.STDOUT,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            try:code=process.wait(timeout=min(limit,remaining))
            except subprocess.TimeoutExpired:
                stop_owned_process_tree(process);record.update(status='timeout',wall_seconds=time.monotonic()-stamp);save();raise TimeoutError('Stage deadline: '+label)
        record.update(status='passed' if code==0 else 'failed',returncode=code,wall_seconds=time.monotonic()-stamp);save()
        if code:raise RuntimeError('Stage failed: '+label+'; see '+str(out/(label+'.log')))
    placement=out/'placement';bound=out/'bound';clock=out/'clock';clock_capture=out/'clock_encoding';routes=out/'route'
    def evaluate(label,route):
        wire=out/(label+'_wire');configuration=out/(label+'_configuration');sta=out/(label+'_export')
        run(label+'_wire','scripts/measure_ownpr_wire_delays.py',['--routes',route,'--out',wire],150)
        run(label+'_configuration','scripts/merge_ownpr_configuration.py',['--placement',placement,'--routes',route,'--clock-capture',clock_capture,'--out',configuration/'partial_configuration.json'],150)
        run(label+'_export','experiments/native_import_ownpr.py',['--placement',placement,'--routes',route,'--wire-timing',wire,'--partial',configuration/'partial_configuration.json',
            '--cst',a.cst.resolve(),'--sdc',a.sdc.resolve(),'--clock-tree',clock/'clock_tree.json','--io-reference',a.io_reference.resolve(),'--export','--sta','--out',sta],180)
        run(label+'_joint','scripts/audit_ownpr_joint_routes.py',['--placement',placement,'--routes',route,'--wire',wire],60)
        check_contract(base,read(sta/'manifest.json'),base_tree,read(sta/'clock_tree.json'),True)
        observed=metric(sta);accepted=improves(result['metrics'],observed);state=dict(placement=str(placement),routes=str(route),wire=str(wire),sta=str(sta))
        result['candidates'].append(dict(**state,metrics=observed,decision='accepted' if accepted else 'rejected_global_timing'))
        if accepted:result.update(selected=state,metrics=observed)
        save();return sta,observed
    save()
    try:
        run('placement','scripts/prepare_ownpr_ioreg_placement.py',['--base',a.placement.resolve(),*[v for port in a.port for v in ('--port',port)],'--out',placement,'--cone-depth',a.cone_depth,'--cone-fraction',a.cone_fraction],60)
        if sha(placement/'packed.json')!=base['packed_sha256']:raise ValueError('Physical packing changed the logical netlist')
        result['logical_netlist_byte_identical']=True
        bind_verified_constraints(placement,base)
        run('bind','scripts/prepare_ownpr_route_inputs.py',['--placement',placement,'--out',bound],60)
        run('clock','experiments/ownpr_clock_candidate.py',['--placement',placement,'--routes',bound,'--cst',a.cst.resolve(),'--sdc',a.sdc.resolve(),'--timing-driven','--out',clock],150)
        if sha(clock/'clock_auto.cst')!=sha(a.cst):raise ValueError('Attempt would change the existing clock routing constraint')
        run('clock_encoding','experiments/native_clock_encoding_probe.py',['--tree',clock/'clock_tree.json','--out',clock_capture],150)
        remaining=int(deadline-time.monotonic()-35)
        if remaining<1:raise TimeoutError('Insufficient remaining routing budget')
        route_limit=min(450,remaining)
        run('route','scripts/run_ownpr_timing_route.py',['--placement',placement,'--clock-tree',clock/'clock_tree.json','--timeout',route_limit,'--out',routes]+(['--reuse-routes',a.routes.resolve()] if a.incremental else []),route_limit+30)
        sta,observed=evaluate('initial',routes)
        if observed['setup_violations'] or observed['hold_violations']:
            eco=out/'eco';run('eco','scripts/ownpr_route_eco.py',['--placement',placement,'--routes',routes,'--sta',sta,'--out',eco],285)
            evaluate('eco',eco)
        result['status']='bounded_ioreg_attempt_complete'
    except Exception as error:
        result.update(status='timeout' if isinstance(error,TimeoutError) else 'failed_stage',error=str(error));save();raise
    save();print(json.dumps(dict(status=result['status'],wall_seconds=result['wall_seconds'],selected=result['selected'],metrics=result['metrics']),indent=2))


if __name__=='__main__':main()
