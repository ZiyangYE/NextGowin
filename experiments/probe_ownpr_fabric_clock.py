"""Own ordinary-wire I/O clock candidate, with all data and core clocks frozen.

This is a physical connectivity/wire-delay experiment, not final STA or a
configured design. No native placement/routing algorithms are executed.
"""
import argparse,json,os,shutil,subprocess,sys,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from gowin_probe import prepare
from ownpr_place import sha
from ownpr_image_storage import load_json
from ownpr_device import DLL_SHA


def worker(out,placement,routes,sta):
    from native_device_session import NativeDeviceSession
    from ownpr_device import Device
    from ownpr_timing_graph import TimingRoutingGraph
    from ownpr_router import route,validate
    from ownpr_expand_routes import expand
    from ownpr_ioreg_macros import validate as validate_ioreg
    from ownpr_clock_partition import partition
    from audit_native_wire_delays import f32
    start=time.monotonic();native=NativeDeviceSession(out);graph=TimingRoutingGraph(Device(),ROOT/'results/native_rrg_001',native)
    design=json.loads((placement/'packed.json').read_text());placed=json.loads((placement/'placement.json').read_text());source_routes=json.loads((routes/'routes.json').read_text());tree=json.loads((routes/'clock_tree.json').read_text())
    allnets=json.loads((routes/'all_nets.json').read_text());ordinary,clock=partition(graph,allnets,tree)
    if ordinary!=json.loads((routes/'nets.json').read_text()):raise ValueError('Baseline clock partition changed')
    info=validate_ioreg(design,placed,graph.device);targets={tuple(r['pins']['CLK']) for r in info['records']}
    if not targets:raise ValueError('No output-register clock endpoints')
    def resource(point):
        r,c,i=point
        if r<1 or c<1 or i not in graph.device.names:return None
        return graph.node(r-1,c-1,graph.device.names[i])
    root=tuple(tree['source']);source=resource(root);sinks=sorted(resource(p) for p in targets)
    core_paths=[p for p in tree['paths_sink_to_source'] if tuple(p[0]) not in targets]
    if {tuple(p[0]) for p in tree['paths_sink_to_source'] if tuple(p[0]) in targets}!=targets:raise ValueError('Baseline omits I/O clock endpoints')
    core_parents={root:None}
    for path in core_paths:
        for i,point in enumerate(path):
            node=tuple(point);parent=tuple(path[i+1]) if i+1<len(path) else None
            if node in core_parents and core_parents[node]!=parent:raise ValueError('Conflicting retained clock parents')
            core_parents[node]=parent
    blocked={n for row in source_routes['nets'].values() for n in row['nodes']}
    blocked.update(n for point in core_parents if (n:=resource(point)) is not None and n!=source)
    for row in info['records']:
        blocked.add(resource(placed['fixed_io'][row['iobuf']]['native_ports']['in']))
    net=dict(name=clock['net'],source=source,sinks=sinks,criticality=1.,clock=True)
    def progress(row):
        with (out/'progress.jsonl').open('a') as log:log.write(json.dumps(row)+'\n')
    result=route(graph,[net],max_iterations=8,timeout=max(1,300-(time.monotonic()-start)),max_expansions=200000,blocked_nodes=blocked,progress=progress)
    (out/'fabric_routes.json').write_text(json.dumps(result));(out/'fabric_nets.json').write_text(json.dumps([net],indent=2))
    if result['status']!='routed':raise ValueError('Fabric clock candidate did not route')
    connectivity=validate(graph,[net],result);physical=expand(graph,[net],result,native.fanouts);(out/'fabric_expanded_routes.json').write_text(json.dumps(physical,indent=2))
    netphys=physical['nets'][clock['net']];parents=dict(core_parents)
    for row in netphys['parents']:
        point=tuple(row['node']);parent=tuple(row['parent']) if row['parent'] else None
        if point in parents and parents[point]!=parent:raise ValueError('Fabric clock conflicts with retained dedicated clock')
        parents[point]=parent
    pointers=native.resolve_routed_nodes(parents);queries={}
    with native.resolved_parent_tree(parents,pointers):
        for point,node in pointers.items():queries[point]=[list(native.delay_pointer(node,s)) for s in range(3)]
    paths=core_paths+[r['path_sink_to_source'] for r in netphys['sinks']];rows=[]
    captures=load_json(sta/'sta_endpoint_audit.json');before={tuple(r['nodes'][0]['node']):r['nodes'][0]['wire_delays'] for r in captures if r['pin']=='CLK' and len(r['nodes'])==1}
    for path in paths:
        sums=[[0.]*4 for _ in range(3)]
        for point in path:
            for s in range(3):sums[s]=[f32(a+b) for a,b in zip(sums[s],queries[tuple(point)][s])]
        endpoint=tuple(path[0]);rows.append(dict(node=list(endpoint),ioreg=endpoint in targets,baseline=before[endpoint],candidate=sums,
            setup_wire_ns=max(sums[1][2:]),hold_wire_ns=min(sums[2][:2]),path_sink_to_source=path))
    compared=dict(status='fabric_clock_wire_candidate_measured',sinks=rows,connectivity=connectivity,retained_core_clock_paths=len(core_paths),fabric_clock_sinks=len(targets),
        frozen_data_nets=len(source_routes['nets']),blocked_resources=len(blocked),native_edges_verified=True,
        baseline_ioreg_max_ns=max(max(before[p][1][2:]) for p in targets),candidate_ioreg_max_ns=max(r['setup_wire_ns'] for r in rows if r['ioreg']),
        max_setup_wire_ns=max(r['setup_wire_ns'] for r in rows),configuration_verified=False,timing_signoff=False,
        scope='Independent ordinary clock routing plus frozen core clock/data occupancy. Full mode/configuration and setup/hold are not validated.')
    (out/'clock_comparison.json').write_text(json.dumps(compared,indent=2));print({k:v for k,v in compared.items() if k not in ('sinks','connectivity')},flush=True)


def main():
    p=argparse.ArgumentParser();p.add_argument('--placement',type=Path,required=True);p.add_argument('--routes',type=Path,required=True);p.add_argument('--sta',type=Path,required=True);p.add_argument('--out',type=Path,required=True);p.add_argument('--worker',action='store_true');a=p.parse_args()
    out=a.out.resolve();placement=a.placement.resolve();routes=a.routes.resolve();sta=a.sta.resolve()
    if a.worker:return worker(out,placement,routes,sta)
    meta=json.loads((sta/'manifest.json').read_text());rm=json.loads((routes/'manifest.json').read_text())
    if (meta['status']!='imported' or not meta['sta_completed'] or meta['packed_sha256']!=sha(placement/'packed.json') or meta['placement_sha256']!=sha(placement/'placement.json')
        or meta['routes_sha256']!=sha(routes/'routes.json') or meta['clock_tree_sha256']!=sha(routes/'clock_tree.json') or rm['status']!='routed' or rm['vendor_pnr_executed']):raise ValueError('Stale or unverified own-P&R baseline')
    prepare(out);shutil.copyfile(__file__,out/'runner_source.py.txt');python=ROOT/'binary_audit/flow/runtime/bin/python.exe';start=time.monotonic();rc=None;expired=False
    with (out/'worker.log').open('w') as log:
        try:rc=subprocess.run([str(python),str(Path(__file__).resolve()),'--worker','--placement',str(placement),'--routes',str(routes),'--sta',str(sta),'--out',str(out)],
            cwd=python.parent,env=dict(os.environ,PYTHONHOME=sys.base_prefix),stdout=log,stderr=subprocess.STDOUT,timeout=330).returncode
        except subprocess.TimeoutExpired:expired=True
    result=dict(status='measured' if rc==0 and (out/'clock_comparison.json').exists() else 'failed',returncode=rc,timeout=expired,wall_seconds=time.monotonic()-start,
        dll_sha256=DLL_SHA,vendor_pnr_executed=False,open_chip_database_used=False,timing_signoff=False,
        source_sha256={str(f):sha(f) for f in (placement/'packed.json',placement/'placement.json',routes/'routes.json',routes/'clock_tree.json',sta/'sta_endpoint_audit.json')})
    if (out/'clock_comparison.json').exists():result['comparison_sha256']=sha(out/'clock_comparison.json')
    (out/'manifest.json').write_text(json.dumps(result,indent=2));print(json.dumps(result,indent=2))
    if result['status']=='failed':raise SystemExit(1)


if __name__=='__main__':main()
