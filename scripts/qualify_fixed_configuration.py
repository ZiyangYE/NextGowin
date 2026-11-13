"""Re-STA/encode an audited fixed configuration, changing only clock period.

Reuse the exact verified partial configuration and measured wire files. The
native importer reserves 600 MB for this handoff; no second configuration or
wire extraction is generated. This is warm qualification, not fresh P&R.
"""
import argparse,json,re,subprocess,sys,time,shutil
from pathlib import Path
from ownpr_device import ROOT
from ownpr_image_storage import load_json,sha
from workspace_budget import reserve
from run_ownpr_pin_closure import metric,stop_owned_process_tree


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for n in ('source-export','placement','routes','cst','sdc','io-reference','out'):p.add_argument('--'+n,type=Path,required=True)
    p.add_argument('--target-mhz',type=float,required=True);p.add_argument('--seconds',type=int,default=240);a=p.parse_args()
    if not 0<a.target_mhz<=1000 or not 1<=a.seconds<=3600:p.error('Invalid warm qualification limits')
    reserve(ROOT,600_000_000);start=time.monotonic();source=load_json(a.source_export/'manifest.json')
    if source['status']!='verified_handoff_complete' or not source['bitstream_generated'] or not source['sta_completed']:raise ValueError('No audited source configuration')
    for path,h in source['source_sha256'].items():
        if sha(Path(path))!=h:raise ValueError('Source implementation changed')
    native_source=load_json(a.source_export/'native/manifest.json')
    from ownpr_io_reference import load as load_io_reference
    io_reference=load_io_reference(a.io_reference,a.cst)
    if native_source['partial_configuration_sha256']!=sha(a.source_export/'partial_configuration.json') or native_source['io_reference_sha256']!=sha(a.source_export/'native/io_reference.json') or load_json(a.source_export/'native/io_reference.json')!=io_reference:raise ValueError('Source configuration/I/O reference differs')
    if source['output_sha256']['handoff_audit.json']!=sha(a.source_export/'native/handoff_audit.json'):raise ValueError('Source handoff audit changed')
    for key,file in (('placement',a.placement),('routes',a.routes)):
        if Path(source['artifacts'][key]).resolve()!=file.resolve():raise ValueError('Different fixed implementation')
    for file in (a.cst,a.sdc):
        if source['source_sha256'].get(str(file.resolve()))!=sha(file):raise ValueError('Source constraints differ')
    a.out.mkdir(exist_ok=False);shutil.copyfile(__file__,a.out/'runner_source.py.txt');clock=a.out/'clock.sdc'
    text,count=re.subn(r'(create_clock\s+-name\s+clk\s+-period\s+)[\d.]+',lambda m:m[1]+format(1000/a.target_mhz,'.14g'),a.sdc.read_text())
    if count!=1:raise ValueError('Not a single primary clock')
    clock.write_text(text);record=dict(status='running',target_mhz=a.target_mhz,only_clock_period_changed=True,fixed_configuration_reused=True,
        original_sdc_sha256=sha(a.sdc),cst_sha256=sha(a.cst),source_export_sha256=sha(a.source_export/'manifest.json'),partial_configuration_sha256=sha(a.source_export/'partial_configuration.json'),vendor_pnr_executed=False)
    def save():record['wall_seconds']=time.monotonic()-start;(a.out/'manifest.json').write_text(json.dumps(record,indent=2))
    args=[sys.executable,str(ROOT/'experiments/native_import_ownpr.py'),'--placement',str(a.placement.resolve()),'--routes',str(a.routes.resolve()),'--wire-timing',str((a.source_export/'wire').resolve()),'--partial',str((a.source_export/'partial_configuration.json').resolve()),'--clock-tree',str((a.routes/'clock_tree.json').resolve()),'--cst',str(a.cst.resolve()),'--sdc',str(clock.resolve()),'--io-reference',str(a.io_reference.resolve()),'--export','--sta','--out',str((a.out/'native').resolve())]
    if load_json(a.placement/'placement.json').get('hard_clusters'):args.append('--experimental-hard-export')
    record['command']=args;save()
    with (a.out/'native.log').open('x') as log:
        proc=subprocess.Popen(args,cwd=ROOT,stdout=log,stderr=subprocess.STDOUT,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        try:code=proc.wait(timeout=max(1,a.seconds-(time.monotonic()-start)))
        except subprocess.TimeoutExpired:stop_owned_process_tree(proc);record.update(status='timeout');save();raise TimeoutError('Warm qualification deadline')
    record.update(returncode=code,status='replayed' if code==0 else 'failed')
    if code:save();raise ValueError('Fixed configuration native import failed')
    record['metrics']=metric(a.out/'native')
    if sha(a.source_export/'partial_configuration.json')!=record['partial_configuration_sha256']:raise ValueError('Configuration changed during replay')
    save();print(json.dumps(record['metrics'],indent=2))


if __name__=='__main__':main()
