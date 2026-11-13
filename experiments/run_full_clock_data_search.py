"""Search actual compatible clock paths with full native-calibrated min/max DAGs."""
import argparse,json,os,shutil,subprocess,sys,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from ownpr_image_storage import load_json,sha,copy_original
from workspace_budget import reserve


def worker(out):
    from native_device_session import NativeDeviceSession
    from ownpr_device import Device
    from ownpr_clock_graph import ClockGraph
    from ownpr_clock_calibration import replay
    from ownpr_clock_timing_search import search
    from ownpr_full_clock_search_model import FullClockSearchModel
    native=NativeDeviceSession(out);cal=replay(native);request=load_json(out/'request.json')
    graph=ClockGraph.for_tree(load_json(out/'baseline_clock_tree.json'))
    model=FullClockSearchModel(load_json(out/'model.json'),load_json(out/'packed.json'),load_json(out/'sta_endpoint_audit.json'))
    expanded=load_json(out/'expanded_routes.json');blocked={tuple(row['node']) for net in expanded['nets'].values() if not net['clock'] for row in net['parents']}
    config=load_json(out/'base_configuration.json')
    if config['conflicts'] or 'clock_configuration_audit' in config:raise ValueError('Expected consistent data-only configuration')
    bits={tuple(r['tile']+r['local_bit']):r['value'] for r in config['bits']}
    tree,measurements,report=search(native,graph,Device(),load_json(out/'baseline_clock_tree.json'),blocked,bits,
        timeout=request['seconds'],slack_model=model,slack_trials=request['trials'])
    for name,payload in [('clock_tree.json',tree),('clock_wire_delays.json',measurements),('search_report.json',dict(report,independent_calibration=cal,full_setup_hold_graph_used=True))]:
        (out/name).write_text(json.dumps(payload,indent=2))


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--out',type=Path,required=True);p.add_argument('--worker',action='store_true')
    for n in ('checkpoint','model','budget'):p.add_argument('--'+n,type=Path)
    p.add_argument('--seconds',type=int,default=180);p.add_argument('--trials',type=int,default=512);a=p.parse_args();out=a.out.resolve()
    if a.worker:return worker(out)
    if not 30<=a.seconds<=300 or not 1<=a.trials<=2048:p.error('Invalid bounded search')
    if time.time()+a.seconds+240>load_json(a.budget)['deadline_epoch']:raise TimeoutError('Insufficient original attempt budget')
    reserve(ROOT,100000000);row=load_json(a.checkpoint)['designs'][0]
    for path,h in row['selected_source_sha256'].items():
        if sha(ROOT/path)!=h:raise ValueError('Source checkpoint changed')
    state={k:ROOT/v for k,v in row['selected'].items()}
    from ownpr_model_reference import load_reference
    load_reference(a.model,state['placement'],state['routes'],state['wire'])
    audit=load_json(state['sta']/'handoff_audit.json')['timing']
    if any(audit['path_coverage'][k]['paths'] for k in ('recovery','removal')):raise ValueError('Async timing classes require a modeled guard')
    from gowin_probe import prepare
    prepare(out);start=time.time();(out/'request.json').write_text(json.dumps(dict(seconds=a.seconds,trials=a.trials,original_budget=load_json(a.budget)),indent=2))
    for source,name in [(state['placement']/'packed.json','packed.json'),(state['routes']/'clock_tree.json','baseline_clock_tree.json'),(state['wire']/'expanded_routes.json','expanded_routes.json'),(state['sta']/'sta_endpoint_audit.json','sta_endpoint_audit.json'),(a.model/'model.json','model.json')]:copy_original(source,out/name)
    from ownpr_configuration import build
    config=build(state['placement'],state['routes']);(out/'base_configuration.json').write_text(json.dumps(config,separators=(',',':')))
    snapshot=out/'sources';snapshot.mkdir()
    for name in ('ownpr_clock_data_model.py','ownpr_full_clock_search_model.py','ownpr_clock_slack_search.py','ownpr_clock_timing_search.py','ownpr_clock_configuration.py','ownpr_bidir_timing.py'):
        shutil.copyfile(ROOT/'scripts'/name,snapshot/name)
    shutil.copyfile(__file__,snapshot/Path(__file__).name)
    python=ROOT/'binary_audit/flow/runtime/bin/python.exe'
    with (out/'worker.log').open('w') as f:
        process=subprocess.run([str(python),str(Path(__file__).resolve()),'--worker','--out',str(out)],cwd=python.parent,
            env=dict(os.environ,PYTHONHOME=sys.base_prefix),stdout=f,stderr=subprocess.STDOUT,timeout=a.seconds+90,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    result=dict(status='actual_clock_candidate_measured' if process.returncode==0 else 'failed',returncode=process.returncode,wall_seconds=time.time()-start,
        original_budget=load_json(a.budget),source_checkpoint_sha256=sha(a.checkpoint),model_sha256=sha(a.model/'model.json'),timing_signoff=False,vendor_pnr_executed=False,open_chip_database_used=False,
        source_sha256={str(f.resolve()):sha(f) for f in [out/'request.json',out/'packed.json',out/'model.json',out/'sta_endpoint_audit.json',out/'baseline_clock_tree.json',out/'expanded_routes.json',out/'base_configuration.json',*snapshot.iterdir()]})
    if process.returncode==0:
        report=load_json(out/'search_report.json');result.update(metrics=report['slack_search']['selected_metrics'],initial=report['slack_search']['initial_metrics'],
            accepted=len(report['slack_search']['accepted']),clock_tree_sha256=sha(out/'clock_tree.json'),search_report_sha256=sha(out/'search_report.json'))
    (out/'manifest.json').write_text(json.dumps(result,indent=2));print(json.dumps({k:v for k,v in result.items() if k!='source_sha256'},indent=2))
    if process.returncode:raise SystemExit(process.returncode)


if __name__=='__main__':main()
