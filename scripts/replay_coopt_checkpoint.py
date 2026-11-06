"""Re-export one reviewed implementation with fixed pins and complete I/O STA.

This is a warm-checkpoint replay, not a fresh RTL synthesis benchmark.
Only the primary clock period may be changed for frequency qualification.
"""
import argparse,json,re,subprocess,sys,time
from pathlib import Path
from ownpr_device import ROOT
from ownpr_place import sha
from workspace_budget import reserve
from run_ownpr_pin_closure import stop_owned_process_tree


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--case',required=True)
    p.add_argument('--out',type=Path,required=True);p.add_argument('--target-mhz',type=float,default=200);p.add_argument('--seconds',type=int,default=300)
    p.add_argument('--report',type=Path,default=ROOT/'results/coopt_comparison_20261104/summary.json');a=p.parse_args()
    if not 0<a.target_mhz<=1000 or not 1<=a.seconds<=3600:p.error('Invalid target or complete replay budget')
    reserve(ROOT,500_000_000);started=time.monotonic();report=json.loads(a.report.read_text())
    row=next(r for r in report['designs'] if r.get('design')==a.case)
    for name,digest in row['selected_source_sha256'].items():
        if sha(ROOT/name)!=digest:raise ValueError('Reviewed checkpoint changed: '+name)
    state={k:ROOT/v for k,v in row['selected'].items()};out=a.out.resolve();out.mkdir(exist_ok=False)
    if 'constraints' in row:
        cst,sdc,reference=(ROOT/row['constraints'][k] for k in ('cst','sdc','io_reference'))
    elif a.case in ('uart','int2float','tmds'):
        cst,sdc,reference=(ROOT/'results/coopt_int2float_rebuild_001/fixed.cst',ROOT/'results/coopt_int2float_rebuild_001/clock200.sdc',ROOT/'results/coopt_int2float_io_reference_002') if a.case=='int2float' else (
            ROOT/f'results/tangprimer138_suite_001/{a.case}/fixed.cst',ROOT/f'results/tangprimer138_suite_001/{a.case}/clock200.sdc',ROOT/('results/native_uart_carry_003' if a.case=='uart' else 'results/coopt_tmds_native_reference_001'))
    else:raise ValueError('A new design must declare its reviewed CST/SDC/I/O reference')
    baseline=json.loads((state['sta']/'manifest.json').read_text())
    if sha(cst)!=baseline['cst_sha256'] or sha(sdc)!=baseline['source_sdc_sha256']:raise ValueError('Reviewed constraint contract changed')
    original=sdc.read_text();changed,n=re.subn(r'(create_clock\s+-name\s+clk\s+-period\s+)[\d.]+',lambda m:m[1]+format(1000/a.target_mhz,'.14g'),original)
    if n!=1:raise ValueError('Replay requires the reviewed single primary clock')
    effective=out/'clock.sdc';effective.write_text(changed)
    cmd=[sys.executable,str(ROOT/'scripts/export_ownpr.py')]
    for name in ('placement','routes','clock_capture'):cmd+=['--'+name.replace('_','-'),str(state[name])]
    cmd+=['--wire-timing',str(state['wire']),'--cst',str(cst),'--sdc',str(effective),'--io-reference',str(reference),'--seconds',str(a.seconds),'--out',str(out/'export')]
    record=dict(status='running',case=a.case,target_mhz=a.target_mhz,command=cmd,original_sdc_sha256=sha(sdc),effective_sdc_sha256=sha(effective),cst_sha256=sha(cst),only_clock_period_changed=True,vendor_pnr_executed=False)
    (out/'manifest.json').write_text(json.dumps(record,indent=2))
    with (out/'export.log').open('w') as log:
        proc=subprocess.Popen(cmd,cwd=ROOT,stdout=log,stderr=subprocess.STDOUT,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        try:code=proc.wait(timeout=max(1,a.seconds-(time.monotonic()-started)))
        except subprocess.TimeoutExpired:stop_owned_process_tree(proc);code=-1
    record.update(status='replayed' if code==0 else 'failed',returncode=code,wall_seconds=time.monotonic()-started)
    if code==0:record['metrics']=json.loads((out/'export/manifest.json').read_text())['metrics']
    (out/'manifest.json').write_text(json.dumps(record,indent=2));print(json.dumps(record,indent=2))
    if code:raise SystemExit(1)


if __name__=='__main__':main()
