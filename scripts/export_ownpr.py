"""Bounded own-P&R handoff to Gowin final STA and its bitstream encoder.

No vendor placement or routing is executed. Generating an image is distinct
from satisfying its timing constraints or verifying it on hardware.
"""
import argparse,json,shutil,subprocess,sys,time
from pathlib import Path
from ownpr_device import ROOT
from ownpr_place import sha
from run_ownpr_pin_closure import metric,stop_owned_process_tree


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('placement','routes','clock-capture','cst','sdc','io-reference','out'):
        p.add_argument('--'+name,type=Path,required=True)
    p.add_argument('--wire-timing',type=Path,help='Reuse measured data routes; native importer checks exact input hashes')
    p.add_argument('--seconds',type=int,default=600)
    a=p.parse_args()
    if not 1<=a.seconds<=3600:p.error('Export budget must be 1..3600 seconds')
    start=time.monotonic();deadline=start+a.seconds
    from workspace_budget import reserve
    reserve(ROOT,1_000_000_000)
    placement=a.placement.resolve();routes=a.routes.resolve();out=a.out.resolve()
    hard=bool(json.loads((placement/'placement.json').read_text()).get('hard_clusters'))
    meta=json.loads((routes/'manifest.json').read_text())
    if (meta['status']!='routed' or meta['vendor_pnr_executed']
            or meta['netlist_sha256']!=sha(placement/'packed.json')
            or meta['placement_sha256']!=sha(placement/'placement.json')):
        raise ValueError('Export requires completed own routing matching this placement and netlist')
    inputs=[placement/'packed.json',placement/'placement.json',routes/'routes.json',routes/'nets.json',routes/'all_nets.json',routes/'clock_tree.json',a.cst,a.sdc]
    sources={str(f.resolve()):sha(f) for f in inputs}
    out.mkdir(exist_ok=False);shutil.copyfile(__file__,out/'runner_source.py.txt')
    result=dict(status='running',budget_seconds=a.seconds,stages=[],source_sha256=sources,
        vendor_placement_executed=False,vendor_routing_executed=False,open_chip_database_used=False,
        sta_completed=False,bitstream_generated=False,native_constraints_satisfied=False,
        timing_signoff=False,full_bitstream_verified=False,device_programmed=False)
    def save():
        result['wall_seconds']=time.monotonic()-start
        (out/'manifest.json').write_text(json.dumps(result,indent=2))
    def run(label,script,args):
        remaining=deadline-time.monotonic()
        if remaining<=0:raise TimeoutError('Complete export deadline exhausted')
        command=[sys.executable,str(ROOT/script),*map(str,args)]
        row=dict(stage=label,status='running',command=command);result['stages'].append(row);save();stamp=time.monotonic()
        with (out/(label+'.log')).open('w') as log:
            process=subprocess.Popen(command,cwd=ROOT,stdout=log,stderr=subprocess.STDOUT,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            try:code=process.wait(timeout=remaining)
            except subprocess.TimeoutExpired:
                stop_owned_process_tree(process);row.update(status='timeout',wall_seconds=time.monotonic()-stamp)
                raise TimeoutError('Export deadline during '+label)
        row.update(status='passed' if code==0 else 'failed',returncode=code,wall_seconds=time.monotonic()-stamp);save()
        if code:raise RuntimeError('Export stage failed: '+label+'; see '+str(out/(label+'.log')))
    save()
    try:
        wire=a.wire_timing.resolve() if a.wire_timing else out/'wire'
        if not a.wire_timing:run('wire','scripts/measure_ownpr_wire_delays.py',['--routes',routes,'--placement',placement,'--out',wire])
        run('joint','scripts/audit_ownpr_joint_routes.py',['--placement',placement,'--routes',routes,'--wire',wire])
        partial=out/'partial_configuration.json';export=out/'native'
        run('configuration','scripts/merge_ownpr_configuration.py',['--placement',placement,'--routes',routes,'--clock-capture',a.clock_capture.resolve(),'--out',partial])
        run('final_sta_encoder','experiments/native_import_ownpr.py',['--placement',placement,'--routes',routes,'--wire-timing',wire,'--partial',partial,
            '--clock-tree',routes/'clock_tree.json','--cst',a.cst.resolve(),'--sdc',a.sdc.resolve(),'--io-reference',a.io_reference.resolve(),'--export','--sta','--out',export]+(['--experimental-hard-export'] if hard else []))
        if any(sha(Path(f))!=value for f,value in sources.items()):raise ValueError('Export inputs changed during the handoff')
        native=json.loads((export/'manifest.json').read_text());observed=metric(export)
        if not native['bitstream_generated']:raise ValueError('Native encoder produced no image')
        result.update(status='verified_handoff_complete',sta_completed=True,bitstream_generated=True,
            calibrated_hard_backend=hard,
            native_constraints_satisfied=observed['native_constraints_satisfied'],metrics=observed,
            artifacts=dict(placement=str(placement),routes=str(routes),wire=str(wire),export=str(export)),
            output_sha256={name:sha(export/name) for name in ('probe.fs','probe.tr','handoff_audit.json','manifest.json')})
    except Exception as error:
        result.update(status='timeout' if isinstance(error,TimeoutError) else 'failed',error=str(error));save();raise
    save();print(json.dumps(result,indent=2))


if __name__=='__main__':main()
