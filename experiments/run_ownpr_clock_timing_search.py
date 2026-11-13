"""Run own dedicated-clock path search with isolated native delay queries."""
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
    from ownpr_clock_timing_search import search
    from ownpr_clock_calibration import replay
    native=NativeDeviceSession(out);calibration=replay(native)
    graph=ClockGraph(ROOT/'results/native_clock_inventory_005');baseline=json.loads((out/'baseline_clock_tree.json').read_text());expanded=json.loads((out/'expanded_routes.json').read_text())
    blocked={tuple(row['node']) for net in expanded['nets'].values() if not net['clock'] for row in net['parents']}
    base=json.loads((out/'base_configuration.json').read_text())
    if base['conflicts']:raise ValueError('Baseline data configuration is conflicting')
    base_bits={tuple(r['tile']+r['local_bit']):r['value'] for r in base['bits']}
    model=None
    if (out/'slack_feedback.json').exists():
        from ownpr_clock_slack import ClockSlackModel
        model=ClockSlackModel(json.loads((out/'slack_feedback.json').read_text()))
    limits=json.loads((out/'slack_search_limits.json').read_text()) if (out/'slack_search_limits.json').exists() else dict(trials=256)
    tree,metrics,report=search(native,graph,Device(),baseline,blocked,base_bits,slack_model=model,slack_trials=limits['trials'])
    report['native_clock_delay_calibration']=calibration
    for name,value in [('clock_tree.json',tree),('clock_wire_delays.json',metrics),('search_report.json',report)]:
        (out/name).write_text(json.dumps(value,indent=2))
    print('CLOCK_SEARCH',report['candidate_paths'],'PATHS',metrics['max_setup_wire_ns'],'MAX_NS',metrics['setup_rise_sink_spread_ns'],'SPREAD_NS',flush=True)


def main():
    p=argparse.ArgumentParser();p.add_argument('--out',type=Path,required=True);p.add_argument('--worker',action='store_true')
    p.add_argument('--placement',type=Path);p.add_argument('--routes',type=Path);p.add_argument('--wire-timing',type=Path);p.add_argument('--baseline-clock',type=Path);p.add_argument('--base-configuration',type=Path)
    p.add_argument('--slack-feedback',type=Path)
    p.add_argument('--slack-trials',type=int,default=256)
    a=p.parse_args();out=a.out.resolve()
    if a.worker:return worker(out)
    if not 1<=a.slack_trials<=2048:p.error('Clock feedback trial budget must be 1..2048')
    if not all((a.placement,a.routes,a.wire_timing,a.baseline_clock,a.base_configuration)):p.error('All frozen input paths are required')
    rm=json.loads((a.routes/'manifest.json').read_text());wm=json.loads((a.wire_timing/'manifest.json').read_text());wire=json.loads((a.wire_timing/'wire_delays.json').read_text())
    if rm['status']!='routed' or rm['netlist_sha256']!=sha(a.placement/'packed.json') or rm['placement_sha256']!=sha(a.placement/'placement.json'):raise ValueError('Clock search placement differs from data routing')
    if wire['source_routes_sha256']!=sha(a.routes/'routes.json') or wm['expanded_routes_sha256']!=sha(a.wire_timing/'expanded_routes.json'):raise ValueError('Data occupancy differs from routed result')
    from ownpr_clock_partition import partition
    from ownpr_device import Device,RoutingGraph
    baseline=json.loads(a.baseline_clock.read_text());ordinary,_=partition(RoutingGraph(Device(),ROOT/'results/native_rrg_001'),json.loads((a.routes/'all_nets.json').read_text()),baseline)
    if ordinary!=json.loads((a.routes/'nets.json').read_text()):raise ValueError('Baseline clock/data partition changed')
    partial=json.loads(a.base_configuration.read_text());hashes=set(partial['source_sha256'].values())
    if 'clock_configuration_audit' in partial:raise ValueError('Clock search requires data-only configuration, without frozen old clock bits')
    if not {sha(a.placement/'packed.json'),sha(a.placement/'placement.json'),sha(a.routes/'routes.json')}<=hashes:raise ValueError('Data configuration source hashes differ')
    prepare(out)
    if a.slack_feedback:
        model=json.loads(a.slack_feedback.read_text())
        if model['status']!='compiled_clock_slack_feedback' or any(sha(Path(f))!=digest for f,digest in model['source_sha256'].items()):raise ValueError('Clock slack feedback evidence changed')
        if model['source_sha256'].get(str(a.placement.resolve()/'packed.json'))!=sha(a.placement/'packed.json'):raise ValueError('Clock slack feedback uses a different placement')
        shutil.copyfile(a.slack_feedback,out/'slack_feedback.json')
        (out/'slack_search_limits.json').write_text(json.dumps(dict(trials=a.slack_trials)))
    for source,name in [(a.baseline_clock,'baseline_clock_tree.json'),(a.wire_timing/'expanded_routes.json','expanded_routes.json'),(a.base_configuration,'base_configuration.json')]:shutil.copyfile(source,out/name)
    shutil.copyfile(__file__,out/'runner_source.py.txt');shutil.copyfile(ROOT/'scripts/ownpr_clock_timing_search.py',out/'search_source.py.txt')
    helpers={}
    for name in ('ownpr_clock_configuration.py','ownpr_clock_slack.py','ownpr_clock_slack_search.py','ownpr_arrival_graph.py'):
        source=ROOT/'scripts'/name;shutil.copyfile(source,out/(name+'.txt'));helpers[str(source)]=sha(source)
    python=ROOT/'binary_audit/flow/runtime/bin/python.exe';start=time.monotonic();rc=None;expired=False
    with (out/'worker.log').open('w') as log:
        try:rc=subprocess.run([str(python),str(Path(__file__).resolve()),'--worker','--out',str(out)],cwd=python.parent,env=dict(os.environ,PYTHONHOME=sys.base_prefix),stdout=log,stderr=subprocess.STDOUT,timeout=180).returncode
        except subprocess.TimeoutExpired:expired=True
    r=dict(status='clock_candidate_selected' if rc==0 and (out/'clock_tree.json').exists() else 'failed',returncode=rc,timeout=expired,wall_seconds=time.monotonic()-start,
        placement_sha256=sha(a.placement/'placement.json'),packed_netlist_sha256=sha(a.placement/'packed.json'),routes_sha256=sha(a.routes/'routes.json'),
        dll_sha256=EXPECTED,vendor_pnr_executed=False,open_chip_database_used=False,configuration_verified=False,timing_signoff=False,
        source_sha256={n:sha(out/n) for n in ('baseline_clock_tree.json','expanded_routes.json','base_configuration.json','runner_source.py.txt','search_source.py.txt')})
    for name in ('clock_tree.json','clock_wire_delays.json','search_report.json'):
        if (out/name).exists():r[name+'_sha256']=sha(out/name)
    if a.slack_feedback:
        for name in ('slack_feedback.json','slack_search_limits.json'):r['source_sha256'][name]=sha(out/name)
    if any(sha(Path(p))!=digest for p,digest in helpers.items()):raise ValueError('Clock helper source changed during the search')
    r['helper_source_sha256']=helpers
    r['source_sha256'].update({Path(p).name+'.txt':digest for p,digest in helpers.items()})
    (out/'manifest.json').write_text(json.dumps(r,indent=2));print(json.dumps(r,indent=2))
    if r['status']=='failed':raise SystemExit(1)


if __name__=='__main__':main()
