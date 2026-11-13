"""Measure own ordinary routes using the isolated native device data service.

No vendor packing, placement, routing or whole-design STA is executed. Cell
arcs, I/O buffers, timing constraints and dedicated resource modes are outside
this wire-only measurement and remain mandatory for timing signoff.
"""
import argparse,json,os,shutil,subprocess,sys,time
from pathlib import Path
from gowin_probe import ROOT,prepare
from run_abc_reproduction import sha
from capture_runtime_tables import EXPECTED


def worker(out,route_folder,placement=None):
    from native_device_session import NativeDeviceSession
    from ownpr_device import Device,RoutingGraph
    from ownpr_expand_routes import expand
    from audit_native_wire_delays import f32
    native=NativeDeviceSession(out)
    # Prove that this initialization reproduces the retained actual-path
    # calibration before querying a different own-generated route tree.
    reference=ROOT/'results/native_wire_delays_009';meta=json.loads((reference/'manifest.json').read_text())
    path=reference/'native_wire_delays.json'
    if meta['dll_sha256']!=EXPECTED or sha(path)!=meta['wire_delays_sha256']:raise ValueError('Timing reference changed')
    reference_data=json.loads(path.read_text());parents={tuple(r['node']):tuple(r['parent']) if r['parent'] else None for r in reference_data['nodes']}
    with native.parent_tree(parents):
        for row in reference_data['nodes']:
            if [list(native.delay(tuple(row['node']),s)) for s in range(3)]!=row['values_by_selector']:
                raise ValueError('Standalone timing service does not reproduce native reference')
    print('TIMING_REFERENCE_MATCHED',len(parents),flush=True)
    edge_mismatches=[];star_mismatches=[];children={n:[] for n in parents}
    for node,parent in parents.items():
        if parent is not None:children[parent].append(node)
    for row in reference_data['nodes']:
        node=tuple(row['node']);parent=tuple(row['parent']) if row['parent'] else None
        local={parent:None,node:parent} if parent is not None else {node:None}
        with native.parent_tree(local):values=[list(native.delay(node,s)) for s in range(3)]
        if values!=row['values_by_selector']:
            edge_mismatches.append(dict(node=row['node'],parent=row['parent'],isolated=values,whole_tree=row['values_by_selector']))
        local.update({child:node for child in children[node]})
        with native.parent_tree(local):star=[list(native.delay(node,s)) for s in range(3)]
        if star!=row['values_by_selector']:
            star_mismatches.append(dict(node=row['node'],parent=row['parent'],star=star,whole_tree=row['values_by_selector']))
    edge_audit=dict(queried_nodes=len(reference_data['nodes']),mismatch_count=len(edge_mismatches),mismatches=edge_mismatches,
        star_mismatch_count=len(star_mismatches),star_mismatches=star_mismatches,
        scope='Isolated parent/child queries versus actual native routed-tree samples; general context independence is not implied.')
    (out/'local_edge_query_audit.json').write_text(json.dumps(edge_audit,indent=2))
    print('ISOLATED_EDGE_QUERY_MISMATCHES',len(edge_mismatches),'STAR',len(star_mismatches),flush=True)
    graph=RoutingGraph(Device(),ROOT/'results/native_rrg_001');nets=json.loads((route_folder/'nets.json').read_text())
    if placement:
        from ownpr_wide_ff_packing import configure_graph
        meta=json.loads((route_folder/'manifest.json').read_text())
        if sha(placement/'packed.json')!=meta['netlist_sha256'] or sha(placement/'placement.json')!=meta['placement_sha256']:raise ValueError('Wire measurement placement changed')
        configure_graph(graph,json.loads((placement/'packed.json').read_text()),json.loads((placement/'placement.json').read_text()))
    routes=json.loads((route_folder/'routes.json').read_text());expanded=expand(graph,nets,routes,native.fanouts)
    (out/'expanded_routes.json').write_text(json.dumps(expanded,indent=2))
    parents={tuple(r['node']):tuple(r['parent']) if r['parent'] else None for n in expanded['nets'].values() for r in n['parents']}
    queried={}
    with native.parent_tree(parents):
        for node in parents:queried[node]=[list(native.delay(node,s)) for s in range(3)]
    measured={}
    for name,net in expanded['nets'].items():
        sinks=[]
        for sink in net['sinks']:
            sums=[[0.]*4 for _ in range(3)]
            for point in sink['path_sink_to_source']:
                for s in range(3):sums[s]=[f32(a+b) for a,b in zip(sums[s],queried[tuple(point)][s])]
            sinks.append(dict(resource=sink['resource'],node=sink['node'],delays_by_selector=sums,
                setup_wire_ns=max(sums[1][2:4]),hold_wire_ns=min(sums[2][:2])))
        measured[name]=dict(clock=net['clock'],sinks=sinks,max_setup_wire_ns=max(s['setup_wire_ns'] for s in sinks),
            min_hold_wire_ns=min(s['hold_wire_ns'] for s in sinks),
            setup_rise_sink_spread_ns=max(s['delays_by_selector'][1][2] for s in sinks)-min(s['delays_by_selector'][1][2] for s in sinks))
    result=dict(status='own_routes_wire_delays_measured',nets=measured,
        nodes=[dict(node=n,values_by_selector=v) for n,v in sorted(queried.items())],
        net_count=len(measured),physical_nodes=len(queried),local_edges=expanded['local_edges'],intrinsic_edges=expanded['intrinsic_edges'],
        calibration_reference_sha256=sha(path),source_routes_sha256=sha(route_folder/'routes.json'),source_nets_sha256=sha(route_folder/'nets.json'),
        vendor_pnr_executed=False,open_chip_database_used=False,timing_signoff=False,
        native_source_sha256=native.source_sha256,speed_context=native.speed,
        scope='Wire-only native data queries on independently generated routes. No I/O cell delay, LUT/FF arc, clock constraint or complete setup/hold signoff.')
    (out/'wire_delays.json').write_text(json.dumps(result,indent=2))
    print('OWN_WIRE_MEASUREMENT',len(measured),'NETS',len(queried),'PHYSICAL_NODES',flush=True)
    for name,net in sorted(measured.items(),key=lambda p:-p[1]['max_setup_wire_ns'])[:8]:
        print(name,'clock',net['clock'],'max_ns',net['max_setup_wire_ns'],'spread_ns',net['setup_rise_sink_spread_ns'],flush=True)


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--routes',type=Path,required=True);ap.add_argument('--out',type=Path,required=True)
    ap.add_argument('--worker',action='store_true');ap.add_argument('--placement',type=Path);args=ap.parse_args();out=args.out.resolve();routes=args.routes.resolve()
    if args.worker:return worker(out,routes,args.placement.resolve() if args.placement else None)
    meta=json.loads((routes/'manifest.json').read_text())
    if meta.get('status')!='routed' or meta.get('vendor_pnr_executed') is not False:raise ValueError('Requires completed own routing')
    prepare(out);shutil.copyfile(__file__,out/'extractor_source.py.txt');python=ROOT/'binary_audit/flow/runtime/bin/python.exe'
    started=time.monotonic();rc=None;timeout=False
    with (out/'worker.log').open('w') as log:
        try:rc=subprocess.run([str(python),str(Path(__file__).resolve()),'--worker','--routes',str(routes),'--out',str(out)]+(['--placement',str(args.placement.resolve())] if args.placement else []),
            cwd=python.parent,env=dict(os.environ,PYTHONHOME=sys.base_prefix),stdout=log,stderr=subprocess.STDOUT,timeout=120).returncode
        except subprocess.TimeoutExpired:timeout=True
    result=dict(status='measured' if rc==0 and (out/'wire_delays.json').exists() else 'failed',returncode=rc,timeout=timeout,
        wall_seconds=time.monotonic()-started,dll_sha256=EXPECTED,vendor_pnr_executed=False,open_chip_database_used=False,timing_signoff=False)
    if 'TA1112' in (out/'worker.log').read_text(errors='replace'):
        result['initialization_diagnostics']=['TA1112 reports default grade 5 despite C1/I0 extended context; reference queries match, diagnostic origin remains unaudited.']
    if result['status']=='measured':result['wire_delays_sha256']=sha(out/'wire_delays.json');result['expanded_routes_sha256']=sha(out/'expanded_routes.json')
    (out/'manifest.json').write_text(json.dumps(result,indent=2));print(json.dumps(result,indent=2))
    if result['status']=='failed':raise SystemExit(1)


if __name__=='__main__':main()
