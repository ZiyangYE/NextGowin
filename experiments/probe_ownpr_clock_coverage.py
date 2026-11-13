"""Measure missing native clock-path coverage; no routing or encoding acceptance."""
import argparse,json,os,shutil,subprocess,sys,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from ownpr_place import sha
from workspace_budget import reserve


def worker(out):
    from native_device_session import NativeDeviceSession
    from ownpr_clock_calibration import replay
    from ownpr_clock_graph import ClockGraph
    from ownpr_clock_coverage import native_paths
    from ownpr_clock_timing_search import resolve_candidates,measure,make_tree
    from ownpr_clock_configuration import ClockChoices
    from ownpr_device import Device
    start=time.monotonic();native=NativeDeviceSession(out);calibration=replay(native);graph=ClockGraph(ROOT/'results/native_clock_inventory_005')
    tree=json.loads((out/'baseline_clock_tree.json').read_text());expanded=json.loads((out/'expanded_routes.json').read_text())
    blocked={tuple(r['node']) for net in expanded['nets'].values() if not net['clock'] for r in net['parents']}
    paths,space=native_paths(graph,tree['source'],[p[0] for p in tree['paths_sink_to_source']],blocked,max_paths=5000)
    oldpaths={graph.by_coordinate[tuple(p[0])]:tuple(graph.by_coordinate[tuple(n)] for n in reversed(p)) for p in tree['paths_sink_to_source']}
    added=0
    for target,path in oldpaths.items():
        if path not in paths[target]:paths[target].append(path);added+=1
    space.update(baseline_paths_added=added,candidate_paths=sum(map(len,paths.values())))
    (out/'path_space.json').write_text(json.dumps(space,indent=2))
    pointers=resolve_candidates(native,graph,paths,tree['source']);codec=ClockChoices(Device(),graph)
    rows=[]
    for target,options in paths.items():
        before=measure(native,graph,[oldpaths[target]],pointers)[0][0];samples=[]
        for path in options:
            if time.monotonic()-start>180:raise TimeoutError('Native coverage query deadline')
            value=measure(native,graph,[path],pointers)[0][0]
            candidate=make_tree(graph,tree['source'],[path]);error=None
            try:
                _,check=codec.requirements(candidate)
                if check['conflicts']:error='configuration_bit_conflicts'
            except ValueError as e:error=str(e)
            samples.append(dict(path_ids=list(path),measurement=value,encoding_error=error))
        best=min(samples,key=lambda r:(r['measurement']['delays_by_selector'][1][2],r['measurement']['setup_wire_ns'],len(r['path_ids'])))
        rows.append(dict(node=graph.nodes[target]['coordinate'],baseline=before,best_rising=best,
            rising_gain_ns=before['delays_by_selector'][1][2]-best['measurement']['delays_by_selector'][1][2],alternatives=samples))
    result=dict(**space,wall_seconds=time.monotonic()-start,native_clock_delay_calibration=calibration,topology_sha256=graph.sha256,
        endpoints=rows,faster_rising_endpoints=sum(r['rising_gain_ns']>1e-6 for r in rows),
        vendor_pnr_executed=False,open_chip_database_used=False,scope='Minimum among sampled per-path native queries, not a global timing bound. No configuration/data conflicts, compatible merge, final STA or hardware acceptance inferred.')
    (out/'coverage_report.json').write_text(json.dumps(result,indent=2));print(result['candidate_paths'],result['faster_rising_endpoints'],flush=True)


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--out',type=Path,required=True);p.add_argument('--worker',action='store_true');p.add_argument('--placement',type=Path);p.add_argument('--routes',type=Path);p.add_argument('--wire',type=Path);a=p.parse_args();out=a.out.resolve()
    if a.worker:return worker(out)
    if not all((a.placement,a.routes,a.wire)):p.error('Require placement, routes and wire')
    from audit_ownpr_joint_routes import audit
    from gowin_probe import prepare
    from capture_runtime_tables import EXPECTED
    from run_ownpr_pin_closure import stop_owned_process_tree
    start=time.monotonic();storage=reserve(ROOT,100_000_000);audit(a.placement,a.routes,a.wire);prepare(out)
    sources={}
    for src,name in ((a.routes/'clock_tree.json','baseline_clock_tree.json'),(a.wire/'expanded_routes.json','expanded_routes.json'),(Path(__file__),'runner_source.py.txt'),(ROOT/'scripts/ownpr_clock_coverage.py','coverage_source.py.txt')):
        shutil.copyfile(src,out/name);sources[name]=sha(out/name)
    with (out/'worker.log').open('w') as log:
        python=ROOT/'binary_audit/flow/runtime/bin/python.exe';process=subprocess.Popen([str(python),str(Path(__file__).resolve()),'--worker','--out',str(out)],cwd=python.parent,env=dict(os.environ,PYTHONHOME=sys.base_prefix),stdout=log,stderr=subprocess.STDOUT,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0));expired=False
        try:code=process.wait(timeout=210)
        except subprocess.TimeoutExpired:stop_owned_process_tree(process);code=None;expired=True
    report=out/'coverage_report.json';result=dict(status='discovery_complete' if code==0 and report.exists() else 'failed',returncode=code,timeout=expired,wall_seconds=time.monotonic()-start,storage_reservation=storage,
        source_sha256=sources,dll_sha256=EXPECTED,report_sha256=sha(report) if report.exists() else None,vendor_pnr_executed=False,open_chip_database_used=False,configuration_verified=False,timing_signoff=False)
    (out/'manifest.json').write_text(json.dumps(result,indent=2));print(json.dumps(result,indent=2))
    if result['status']=='failed':raise SystemExit(1)


if __name__=='__main__':main()
