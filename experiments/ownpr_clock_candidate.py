"""Own clock-tree connectivity search and native-data timing calibration.

This deliberately emits an unconfigured candidate, not an accepted P&R image.
The original CST is retained and the sole clock-policy change is recorded.
"""
import argparse,json,os,shutil,subprocess,sys,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from gowin_probe import prepare
from run_abc_reproduction import sha
from capture_runtime_tables import EXPECTED


def worker(out):
    from native_device_session import NativeDeviceSession
    from audit_native_wire_delays import f32
    from ownpr_clock_calibration import replay
    native=NativeDeviceSession(out);calibration=replay(native)
    (out/'timing_calibration.json').write_text(json.dumps(calibration,indent=2))
    if (out/'timing_driven.json').exists():
        from ownpr_clock_timing_search import search
        from ownpr_clock_graph import ClockGraph
        from ownpr_device import Device
        baseline=json.loads((out/'clock_tree.json').read_text());graph=ClockGraph(ROOT/'results/native_clock_inventory_005')
        tree,_,report=search(native,graph,Device(),baseline,(),{})
        (out/'bfs_clock_tree.json').write_text(json.dumps(baseline,indent=2));(out/'clock_tree.json').write_text(json.dumps(tree,indent=2));(out/'search_report.json').write_text(json.dumps(report,indent=2))
    tree=json.loads((out/'clock_tree.json').read_text());parents={tuple(n['node']):tuple(n['parent']) if n['parent'] else None for n in tree['parents']}
    pointers=native.resolve_routed_nodes(parents);values={}
    with native.resolved_parent_tree(parents,pointers):
        for node,pointer in pointers.items():values[node]=[list(native.delay_pointer(pointer,s)) for s in range(3)]
    sinks=[]
    for chain in tree['paths_sink_to_source']:
        sums=[[0.]*4 for _ in range(3)]
        for node in chain:
            for s in range(3):sums[s]=[f32(a+b) for a,b in zip(sums[s],values[tuple(node)][s])]
        sinks.append(dict(node=chain[0],delays_by_selector=sums,setup_wire_ns=max(sums[1][2:]),hold_wire_ns=min(sums[2][:2])))
    result=dict(status='unconfigured_clock_tree_measured',sinks=sinks,
        max_setup_wire_ns=max(s['setup_wire_ns'] for s in sinks),
        setup_rise_sink_spread_ns=max(s['delays_by_selector'][1][2] for s in sinks)-min(s['delays_by_selector'][1][2] for s in sinks),
        nodes=[dict(node=n,values_by_selector=v) for n,v in values.items()],
        vendor_pnr_executed=False,configuration_verified=False,timing_signoff=False,
        scope='Native data queries on an independently selected dedicated-clock tree. Mux/source mode encoding, shared resource legality and full I/O STA remain unverified.')
    (out/'clock_wire_delays.json').write_text(json.dumps(result,indent=2))
    print('OWN_CLOCK_QUERY',len(sinks),'SINKS','MAX_NS',result['max_setup_wire_ns'],'RISE_SPREAD_NS',result['setup_rise_sink_spread_ns'],flush=True)


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--out',type=Path,required=True);ap.add_argument('--worker',action='store_true')
    ap.add_argument('--timing-driven',action='store_true')
    ap.add_argument('--placement',type=Path,default=ROOT/'results/ownpr_int2float_place_004');ap.add_argument('--routes',type=Path,default=ROOT/'results/ownpr_int2float_route_008')
    ap.add_argument('--cst',type=Path,default=ROOT/'results/int2float_clock_ablation_001/lut4.cst');ap.add_argument('--sdc',type=Path,default=ROOT/'results/fullio_closure_003/int_lut4_local155_edge.sdc')
    args=ap.parse_args();out=args.out.resolve()
    if args.worker:return worker(out)
    from ownpr_device import Device,RoutingGraph
    from ownpr_clock_graph import ClockGraph
    from ownpr_clock_policy import allow_auto_clock
    from ownpr_place import constraints
    prepare(out);shutil.copyfile(__file__,out/'runner_source.py.txt');started=time.monotonic()
    if args.timing_driven:(out/'timing_driven.json').write_text('{}')
    placement=args.placement.resolve();routes=args.routes.resolve();cst=args.cst.resolve();sdc=args.sdc.resolve()
    design=json.loads((placement/'packed.json').read_text());pm=json.loads((placement/'manifest.json').read_text())
    rm=json.loads((routes/'manifest.json').read_text())
    if sha(cst)!=pm['cst_sha256'] or sha(sdc)!=pm['sdc_sha256'] or sha(placement/'placement.json')!=rm['placement_sha256']:
        raise ValueError('Frozen input contract changed')
    original=cst.read_text();before=constraints(design['modules']['top'],original,sdc.read_text())
    if len(before['clock_routing'])>1:raise ValueError('Multiple explicit clock policies not supported')
    automatic=allow_auto_clock(original,before['clock_routing'][0]['net']) if before['clock_routing'] else original
    after=constraints(design['modules']['top'],automatic,sdc.read_text())
    for key in ('pins','properties','ports','period_ns','clock_port'):
        if before[key]!=after[key]:raise ValueError('Clock policy changed external interface contract')
    (out/'original.cst').write_text(original);(out/'clock_auto.cst').write_text(automatic)
    graph=RoutingGraph(Device(),ROOT/'results/native_rrg_001');clock_graph=ClockGraph(ROOT/'results/native_clock_inventory_005')
    netfile=routes/'all_nets.json' if (routes/'all_nets.json').exists() else routes/'nets.json'
    clocks=[n for n in json.loads(netfile.read_text()) if n.get('clock')]
    if len(clocks)!=1:raise ValueError('Initial clock candidate supports one domain')
    clock=clocks[0]
    def point(node):
        r,c=graph[node];return r+1,c+1,node%graph.n
    source=point(clock['source']);sinks=[point(n) for n in clock['sinks']]
    if source!=tuple(clock_graph.data['fixed_clock_source']['node']['coordinate']):raise ValueError('Clock source differs from fixed-pin inventory')
    tree=clock_graph.candidate(source,sinks,'AUTO');(out/'clock_tree.json').write_text(json.dumps(tree,indent=2))
    reference=json.loads((ROOT/'results/native_global_clock_001/native_endpoint_routes.json').read_text())
    paths=[s['path_sink_to_source'] for n in reference['nets'] for s in n['sinks'] if s.get('native_pin_type')==670]
    check=clock_graph.audit_paths(paths);(out/'native_clock_path_audit.json').write_text(json.dumps(check,indent=2))
    if check['status']!='clock_paths_match':raise ValueError('Clock graph does not explain native reference')
    python=ROOT/'binary_audit/flow/runtime/bin/python.exe';rc=None;expired=False
    with (out/'worker.log').open('w') as log:
        try:rc=subprocess.run([str(python),str(Path(__file__).resolve()),'--worker','--out',str(out)],cwd=python.parent,
            env=dict(os.environ,PYTHONHOME=sys.base_prefix),stdout=log,stderr=subprocess.STDOUT,timeout=120).returncode
        except subprocess.TimeoutExpired:expired=True
    result=dict(status='unconfigured_candidate_measured' if rc==0 and (out/'clock_wire_delays.json').exists() else 'failed',returncode=rc,timeout=expired,
        wall_seconds=time.monotonic()-started,dll_sha256=EXPECTED,vendor_pnr_executed=False,open_chip_database_used=False,
        placement_sha256=sha(placement/'placement.json'),packed_netlist_sha256=sha(placement/'packed.json'),
        original_cst_sha256=sha(cst),proposed_cst_sha256=sha(out/'clock_auto.cst'),sdc_sha256=sha(sdc),
        clock_policy_change=dict(before='LOCAL_CLOCK' if before['clock_routing'] else 'AUTO',after='AUTO',pins_and_period_unchanged=True),
        configuration_verified=False,timing_signoff=False,full_bitstream_verified=False)
    result['timing_driven_search']=args.timing_driven
    for name in ('clock_tree.json','clock_wire_delays.json','timing_calibration.json'):
        if (out/name).exists():result[name+'_sha256']=sha(out/name)
    (out/'manifest.json').write_text(json.dumps(result,indent=2));print(json.dumps(result,indent=2))
    if result['status']=='failed':raise SystemExit(1)


if __name__=='__main__':main()
