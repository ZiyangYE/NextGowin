"""Compare own routed designs with frozen packing/placement and real queries."""
import argparse,hashlib,json,statistics
from pathlib import Path
from ownpr_image_storage import load_json,sha


def load(routes,measurement):
    routes=Path(routes);measurement=Path(measurement)
    meta=json.loads((routes/'manifest.json').read_text());probe=json.loads((measurement/'manifest.json').read_text())
    path=measurement/'wire_delays.json';data=load_json(path)
    if (meta.get('status')!='routed' or meta.get('vendor_pnr_executed') is not False
            or probe.get('status')!='measured' or probe.get('vendor_pnr_executed') is not False
            or meta.get('open_chip_database_used') is not False or probe.get('open_chip_database_used') is not False
            or sha(path)!=probe.get('wire_delays_sha256')
            or data['source_routes_sha256']!=sha(routes/'routes.json') or data['source_nets_sha256']!=sha(routes/'nets.json')):
        raise ValueError('Unverified own route measurement')
    config=load_json(routes/'partial_configuration.json')
    root=Path(__file__).resolve().parents[1]
    matches=[digest for name,digest in config['source_sha256'].items()
        if (Path(name) if Path(name).is_absolute() else root/name).resolve()==routes/'routes.json']
    if matches!=[sha(routes/'routes.json')]:
        raise ValueError('Configuration audit is for different routes')
    if config['status']!='partial_configuration_consistent' or config['conflicts'] or not config['raw_pip_writes_calibrated']:
        raise ValueError('Partial configuration checks do not pass')
    return meta,probe,data,config


def compare(baseline_routes,baseline_measurement,candidate_routes,candidate_measurement,placement):
    baseline_routes=Path(baseline_routes).resolve();candidate_routes=Path(candidate_routes).resolve();placement=Path(placement).resolve()
    am,ap,a,ac=load(baseline_routes,baseline_measurement);bm,bp,b,bc=load(candidate_routes,candidate_measurement)
    if am['placement_sha256']!=bm['placement_sha256'] or am['netlist_sha256']!=bm['netlist_sha256']:
        raise ValueError('Packing or placement differs; not a routing-only comparison')
    if bm['placement_sha256']!=sha(placement/'placement.json') or bm['netlist_sha256']!=sha(placement/'packed.json'):
        raise ValueError('Frozen placement artifact changed')
    pm=json.loads((placement/'manifest.json').read_text())
    if not pm['all_ports_fixed'] or not pm['all_nonclock_io_max_min_constrained']:raise ValueError('Incomplete frozen I/O contract')
    if set(a['nets'])!=set(b['nets']):raise ValueError('Logical net coverage differs')
    clocks=[];rows=[];improved=regressed=unchanged=hold_shorter=0
    for name,x in a['nets'].items():
        y=b['nets'][name]
        xs={s['resource']:s for s in x['sinks']};ys={s['resource']:s for s in y['sinks']}
        if set(xs)!=set(ys) or x['clock']!=y['clock']:raise ValueError('Endpoint/clock binding changed')
        for sink in xs:hold_shorter+=ys[sink]['hold_wire_ns']<xs[sink]['hold_wire_ns']-1e-6
        if x['clock']:
            clocks.append(dict(net=name,baseline_max_ns=x['max_setup_wire_ns'],candidate_max_ns=y['max_setup_wire_ns'],
                baseline_rise_spread_ns=x['setup_rise_sink_spread_ns'],candidate_rise_spread_ns=y['setup_rise_sink_spread_ns']))
        else:
            delta=y['max_setup_wire_ns']-x['max_setup_wire_ns'];improved+=delta<-1e-6;regressed+=delta>1e-6;unchanged+=abs(delta)<=1e-6
            rows.append(dict(net=name,baseline_ns=x['max_setup_wire_ns'],candidate_ns=y['max_setup_wire_ns'],delta_ns=delta))
    return dict(status='wire_only_comparison_verified',baseline_routes=str(baseline_routes),candidate_routes=str(candidate_routes),
        placement_sha256=bm['placement_sha256'],packed_netlist_sha256=bm['netlist_sha256'],
        cst_sha256=pm['cst_sha256'],sdc_sha256=pm['sdc_sha256'],clocks=clocks,
        data_net_count=len(rows),data_peak_ns=dict(baseline=max(r['baseline_ns'] for r in rows),candidate=max(r['candidate_ns'] for r in rows)),
        data_mean_of_net_peaks_ns=dict(baseline=statistics.mean(r['baseline_ns'] for r in rows),candidate=statistics.mean(r['candidate_ns'] for r in rows)),
        data_net_outcomes=dict(improved=improved,regressed=regressed,unchanged=unchanged),
        regressions=sorted([r for r in rows if r['delta_ns']>1e-6],key=lambda r:-r['delta_ns']),
        hold_wire_delay_shortened_endpoints=hold_shorter,
        pips=dict(baseline=a['local_edges'],candidate=b['local_edges']),
        route_wall_seconds=dict(baseline=am.get('total_wall_seconds',am['wall_seconds']),candidate=bm.get('total_wall_seconds',bm['wall_seconds'])),
        measurement_wall_seconds=bp['wall_seconds'],partial_configuration=dict(status=bc['status'],conflicts=len(bc['conflicts']),bits=len(bc['bits'])),
        initialization_diagnostics=bp.get('initialization_diagnostics',[]),
        timing_signoff=False,fmax_claimed=False,
        scope='Same packing/placement/frozen-I/O route comparison. Wire-only improvement is not whole-path setup/hold or bitstream acceptance; shortened hold delays require full STA.')


if __name__=='__main__':
    ap=argparse.ArgumentParser()
    for name in ('baseline-routes','baseline-measurement','candidate-routes','candidate-measurement','placement','out'):
        ap.add_argument('--'+name,type=Path,required=True)
    args=ap.parse_args();result=compare(args.baseline_routes,args.baseline_measurement,args.candidate_routes,args.candidate_measurement,args.placement)
    args.out.write_text(json.dumps(result,indent=2));print(json.dumps({k:v for k,v in result.items() if k!='regressions'},indent=2))
