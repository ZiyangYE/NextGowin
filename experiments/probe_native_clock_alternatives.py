"""Measure bounded native clock paths before assigning encoding semantics."""
import argparse,json,os,shutil,subprocess,sys,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from gowin_probe import prepare
from ownpr_place import sha
from ownpr_device import DLL_SHA


def worker(out):
    from native_device_session import NativeDeviceSession
    from ownpr_device import Device
    from ownpr_clock_graph import ClockGraph
    from ownpr_clock_diagnostics import sample_paths
    from ownpr_clock_timing_search import resolve_candidates,measure,make_tree
    from ownpr_clock_configuration import ClockChoices
    native=NativeDeviceSession(out);g=ClockGraph(ROOT/'results/native_clock_inventory_005');d=Device();baseline=json.loads((out/'baseline_clock_tree.json').read_text())
    source=baseline['source'];sink=[109,135,124];paths,budget=sample_paths(g,source,sink)
    baseline_path=tuple(g.by_coordinate[tuple(n)] for n in reversed(next(p for p in baseline['paths_sink_to_source'] if p[0]==sink)))
    if baseline_path not in paths:paths.append(baseline_path)
    pointers=resolve_candidates(native,g,{g.by_coordinate[tuple(sink)]:paths},source);choices=ClockChoices(d,g);rows=[]
    for index,path in enumerate(paths):
        measured,values=measure(native,g,[path],pointers);row=measured[0];tree=make_tree(g,source,[path]);status='known_conflict_free';error=None
        try:
            _,check=choices.requirements(tree)
            if check['conflicts']:status='known_configuration_conflict'
        except ValueError as e:status='unimplemented_configuration';error=str(e)
        rows.append(dict(index=index,**row,existing_baseline_path=path==baseline_path,configuration_status=status,configuration_error=error,
            path_sink_to_source=[dict(g.nodes[n],values_by_selector=values[tuple(g.nodes[n]['coordinate'])]) for n in reversed(path)]))
    rows.sort(key=lambda r:(r['setup_wire_ns'],r['index']));result=dict(status='unconfigured_native_paths_measured',source=source,sink=sink,paths=rows,budget=budget,
        topology_sha256=g.sha256,vendor_pnr_executed=False,configuration_verified=False,timing_signoff=False,
        scope='Bounded simple native paths, including template/unknown clock modes. Low query delay alone does not establish a realizable route.')
    (out/'alternatives.json').write_text(json.dumps(result,indent=2));print('ALTERNATIVES',len(rows),[(r['setup_wire_ns'],r['configuration_status']) for r in rows[:5]],flush=True)


def main():
    p=argparse.ArgumentParser();p.add_argument('--baseline-clock',type=Path,required=True);p.add_argument('--out',type=Path,required=True);p.add_argument('--worker',action='store_true');a=p.parse_args();out=a.out.resolve()
    if a.worker:return worker(out)
    prepare(out);shutil.copyfile(a.baseline_clock,out/'baseline_clock_tree.json');shutil.copyfile(__file__,out/'runner_source.py.txt');python=ROOT/'binary_audit/flow/runtime/bin/python.exe';start=time.monotonic();rc=None;expired=False
    with (out/'worker.log').open('w') as log:
        try:rc=subprocess.run([str(python),str(Path(__file__).resolve()),'--worker','--baseline-clock',str(a.baseline_clock.resolve()),'--out',str(out)],cwd=python.parent,env=dict(os.environ,PYTHONHOME=sys.base_prefix),stdout=log,stderr=subprocess.STDOUT,timeout=120).returncode
        except subprocess.TimeoutExpired:expired=True
    result=dict(status='measured' if rc==0 and (out/'alternatives.json').exists() else 'failed',returncode=rc,timeout=expired,wall_seconds=time.monotonic()-start,
        dll_sha256=DLL_SHA,vendor_pnr_executed=False,open_chip_database_used=False,configuration_verified=False,timing_signoff=False,baseline_clock_sha256=sha(out/'baseline_clock_tree.json'))
    if (out/'alternatives.json').exists():result['alternatives_sha256']=sha(out/'alternatives.json')
    (out/'manifest.json').write_text(json.dumps(result,indent=2));print(json.dumps(result,indent=2))
    if result['status']=='failed':raise SystemExit(1)


if __name__=='__main__':main()
