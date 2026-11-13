"""Sample attainable dedicated-clock latency for own placement candidates."""
import argparse,json,os,shutil,subprocess,sys,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from gowin_probe import prepare
from run_abc_reproduction import sha
from capture_runtime_tables import EXPECTED


def worker(out):
    from native_device_session import NativeDeviceSession
    from ownpr_device import Device
    from ownpr_clock_graph import ClockGraph
    from ownpr_clock_timing_search import candidate_space,resolve_candidates,measure,make_tree
    from ownpr_clock_configuration import ClockChoices
    native=NativeDeviceSession(out);device=Device();g=ClockGraph(ROOT/'results/native_clock_inventory_005');request=json.loads((out/'request.json').read_text());source=request['source']
    # Replay the independently retained reference before using the clock model.
    from ownpr_clock_calibration import replay
    calibration=replay(native)
    paths,space=candidate_space(g,device,source,request['sinks'],max_paths=50000);pointers=resolve_candidates(native,g,paths,source);choices=ClockChoices(device,g);rows=[];start=time.monotonic()
    for target,options in paths.items():
        scores=[]
        for path in options:
            if time.monotonic()-start>240:raise TimeoutError('Clock-field sampling time budget exceeded')
            try:_,check=choices.requirements(make_tree(g,source,[path]))
            except ValueError:continue
            if check['conflicts']:continue
            result,_=measure(native,g,[path],pointers);scores.append((result[0]['setup_wire_ns'],path,result[0]))
        if not scores:raise ValueError('No encodable path for placement candidate')
        _,path,row=min(scores,key=lambda x:(x[0],x[1]));row['path_sink_to_source']=[g.nodes[n]['coordinate'] for n in reversed(path)];rows.append(row)
    result=dict(status='clock_placement_field_measured',source=source,sites=rows,space=space,topology_sha256=g.sha256,reference_sha256=calibration['reference_sha256'],native_clock_delay_calibration=calibration,
        dll_sha256=EXPECTED,vendor_pnr_executed=False,configuration_verified=False,timing_signoff=False,
        scope='Individual endpoint clock candidates for placement guidance. Merged tree, source modes, encoding and full I/O STA remain to be checked.')
    (out/'clock_field.json').write_text(json.dumps(result,indent=2));print('CLOCK_FIELD',len(rows),'SITES',space['candidate_paths'],'PATHS',time.monotonic()-start,'SECONDS',flush=True)


def main():
    p=argparse.ArgumentParser();p.add_argument('--out',type=Path,required=True);p.add_argument('--worker',action='store_true');p.add_argument('--placement',type=Path);p.add_argument('--sinks',type=Path);p.add_argument('--clock-tree',type=Path);a=p.parse_args();out=a.out.resolve()
    if a.worker:return worker(out)
    from ownpr_clock_graph import ClockGraph
    if not a.clock_tree or bool(a.placement)==bool(a.sinks):p.error('Require a clock tree and exactly one of --placement or --sinks')
    g=ClockGraph(ROOT/'results/native_clock_inventory_005');tree=json.loads(a.clock_tree.read_text());provenance={}
    if a.sinks:
        sinks=json.loads(a.sinks.read_text())
        if not sinks or any(not isinstance(s,list) or len(s)!=3 or any(type(v) is not int for v in s) or tuple(s) not in g.by_coordinate for s in sinks):raise ValueError('Unknown or invalid requested clock endpoint')
        if len({tuple(s) for s in sinks})!=len(sinks):raise ValueError('Duplicate requested clock endpoint')
        provenance['requested_sinks_sha256']=sha(a.sinks)
    else:
        placed=json.loads((a.placement/'placement.json').read_text());sites=[(r['row'],r['column']) for r in placed['clusters'].values() if r['ff']]
        rmin=max(0,min(r for r,c in sites)-4);rmax=min(108,max(r for r,c in sites)+4);cmin=max(0,min(c for r,c in sites)-16);cmax=min(181,max(c for r,c in sites)+16)
        sinks=[[r+1,c+1,124] for r in range(rmin,rmax+1) for c in range(cmin,cmax+1) if (r+1,c+1,124) in g.by_coordinate]
        provenance['placement_sha256']=sha(a.placement/'placement.json')
    prepare(out);(out/'request.json').write_text(json.dumps(dict(source=tree['source'],sinks=sinks,clock_tree_sha256=sha(a.clock_tree),**provenance),indent=2));shutil.copyfile(__file__,out/'runner_source.py.txt')
    python=ROOT/'binary_audit/flow/runtime/bin/python.exe';start=time.monotonic();rc=None;expired=False
    with (out/'worker.log').open('w') as log:
        try:rc=subprocess.run([str(python),str(Path(__file__).resolve()),'--worker','--out',str(out)],cwd=python.parent,env=dict(os.environ,PYTHONHOME=sys.base_prefix),stdout=log,stderr=subprocess.STDOUT,timeout=300).returncode
        except subprocess.TimeoutExpired:expired=True
    result=dict(status='measured' if rc==0 and (out/'clock_field.json').exists() else 'failed',returncode=rc,timeout=expired,wall_seconds=time.monotonic()-start,
        dll_sha256=EXPECTED,vendor_pnr_executed=False,open_chip_database_used=False,request_sha256=sha(out/'request.json'),runner_sha256=sha(out/'runner_source.py.txt'))
    if (out/'clock_field.json').exists():result['field_sha256']=sha(out/'clock_field.json')
    (out/'manifest.json').write_text(json.dumps(result,indent=2));print(json.dumps(result,indent=2))
    if result['status']!='measured':raise SystemExit(1)


if __name__=='__main__':main()
