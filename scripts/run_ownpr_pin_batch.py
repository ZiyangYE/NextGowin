"""One deadline for fresh native feedback, joint pin mapping, CEC and full STA.

This is an incremental optimization from a checked own-P&R implementation,
not a cold RTL benchmark. A failed earlier exploration is not rehabilitated.
"""
import argparse,json,re,shutil,subprocess,sys,time
from datetime import datetime,timezone
from pathlib import Path
from ownpr_device import ROOT
from ownpr_image_storage import load_json,sha
from run_ownpr_pin_closure import stop_owned_process_tree
from workspace_budget import reserve


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--report',type=Path,required=True);p.add_argument('--case',default='vexriscv')
    p.add_argument('--target-mhz',type=float,required=True);p.add_argument('--window',type=float,default=1.)
    p.add_argument('--count',type=int,default=2);p.add_argument('--seconds',type=int,default=1200)
    p.add_argument('--out',type=Path,required=True);a=p.parse_args()
    if not 0<a.target_mhz<=1000 or not 0<a.window<=5 or not 2<=a.count<=4 or not 600<=a.seconds<=3500:p.error('Invalid bounded run')
    start=time.monotonic();started=datetime.now(timezone.utc).isoformat();reserve(ROOT,1_600_000_000)
    out=a.out.resolve();out.mkdir(exist_ok=False)
    shutil.copyfile(__file__,out/'runner_source.py.txt')
    result=dict(status='running',started_utc=started,budget_seconds=a.seconds,stages=[],target_mhz=a.target_mhz,
        vendor_pnr_executed=False,open_chip_database_used=False,cold_rtl_run=False,device_programmed=False,
        source_report_sha256=sha(a.report),runner_sha256=sha(out/'runner_source.py.txt'))
    def save():
        result['wall_seconds']=time.monotonic()-start
        (out/'manifest.json').write_text(json.dumps(result,indent=2)+'\n')
    def remaining():return a.seconds-(time.monotonic()-start)
    def run(label,script,args):
        if remaining()<=0:raise TimeoutError('Complete attempt deadline exhausted')
        cmd=[sys.executable,str(ROOT/script),*map(str,args)];stamp=time.monotonic()
        row=dict(stage=label,command=cmd,status='running');result['stages'].append(row);save()
        with (out/(label+'.log')).open('w') as log:
            proc=subprocess.Popen(cmd,cwd=ROOT,stdout=log,stderr=subprocess.STDOUT,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            try:rc=proc.wait(timeout=max(.001,remaining()))
            except subprocess.TimeoutExpired:
                stop_owned_process_tree(proc);row.update(status='timeout',wall_seconds=time.monotonic()-stamp);raise
        row.update(status='complete' if rc==0 else 'failed',returncode=rc,wall_seconds=time.monotonic()-stamp);save()
        if rc:raise RuntimeError('Stage failed: '+label)
    save()
    try:
        row=next(r for r in load_json(a.report)['designs'] if r['design']==a.case)
        for name,digest in row['selected_source_sha256'].items():
            if sha(ROOT/name)!=digest:raise ValueError('Reviewed state changed: '+name)
        state={k:ROOT/v for k,v in row['selected'].items()};constraints={k:ROOT/v for k,v in row['constraints'].items()}
        native=load_json(state['sta']/'manifest.json')
        if native['cst_sha256']!=sha(constraints['cst']) or native['source_sdc_sha256']!=sha(constraints['sdc']):raise ValueError('Constraint contract changed')
        sdc,count=re.subn(r'(create_clock\s+-name\s+clk\s+-period\s+)[\d.]+',lambda m:m[1]+format(1000/a.target_mhz,'.14g'),constraints['sdc'].read_text())
        if count!=1:raise ValueError('Exactly one reviewed primary clock required')
        effective=out/'clock.sdc';effective.write_text(sdc)
        result.update(original_sdc_sha256=sha(constraints['sdc']),effective_sdc_sha256=sha(effective),only_clock_period_changed=True)
        capture=out/'feedback';candidate=out/'candidate';export=out/'export'
        run('capture','experiments/native_pin_feedback_probe.py',['--source',state['sta'],'--placement',state['placement'],'--out',capture])
        run('candidate','scripts/ownpr_lut_pin_eco.py',['--placement',state['placement'],'--routes',state['routes'],'--sta',state['sta'],
            '--pin-feedback',capture,'--window',a.window,'--batch-count',a.count,'--out',candidate])
        if remaining()<300:raise TimeoutError('Insufficient reserved time for complete checks/export')
        run('export','scripts/export_ownpr.py',['--placement',candidate/'placement','--routes',candidate,'--clock-capture',state['clock_capture'],
            '--cst',constraints['cst'],'--sdc',effective,'--io-reference',constraints['io_reference'],'--seconds',int(remaining())-1,'--out',export])
        em=load_json(export/'manifest.json');nm=load_json(export/'native/manifest.json');cm=load_json(candidate/'manifest.json');metrics=em['metrics']
        if em['status']!='verified_handoff_complete' or not metrics['native_constraints_satisfied'] or not metrics['other_checks_passed'] or metrics['setup_violations'] or metrics['hold_violations']:raise ValueError('Full timing qualification failed')
        if nm['packed_sha256']!=sha(candidate/'placement/packed.json') or nm['routes_sha256']!=sha(candidate/'routes.json'):raise ValueError('Export input mismatch')
        if sha(candidate/'placement/placement.json')!=sha(state['placement']/'placement.json') or sha(candidate/'clock_tree.json')!=sha(state['routes']/'clock_tree.json'):raise ValueError('Unexpected physical movement')
        if nm['cst_sha256']!=sha(constraints['cst']) or nm['source_sdc_sha256']!=sha(effective):raise ValueError('Export constraint mismatch')
        if not all(r['cec']['equivalent'] for r in cm['cec']):raise ValueError('Missing CEC')
        result.update(status='qualified',metrics=metrics,placement_unchanged=True,clock_tree_unchanged=True,
            cec_observations=sum(r['observed_hard_inputs_and_ports'] for r in cm['cec']),selected_permutations=[dict(instance=r['instance'],old_input=r['old_input'],new_input=r['new_input'],gain_ns=r['gain_ns']) for r in cm['pin_permutations']])
        rel=lambda x:Path(x).relative_to(ROOT).as_posix()
        selected=dict(placement=rel(candidate/'placement'),routes=rel(candidate),wire=rel(export/'wire'),sta=rel(export/'native'),clock_capture=rel(state['clock_capture']))
        files=[candidate/'placement/packed.json',candidate/'placement/placement.json',*[candidate/n for n in ('routes.json','nets.json','all_nets.json','clock_tree.json')],*[export/'native'/n for n in ('manifest.json','probe.tr','handoff_audit.json')],export/'wire/wire_delays.json']
        checkpoint=dict(designs=[dict(design=a.case,selected=selected,metrics=metrics,constraints=dict(cst=rel(constraints['cst']),sdc=rel(effective),io_reference=rel(constraints['io_reference'])),selected_source_sha256={rel(f):sha(f) for f in files})])
        if remaining()<=0:raise TimeoutError('Checks exceeded complete attempt deadline')
        (out/'qualified_replay_checkpoint.json').write_text(json.dumps(checkpoint,indent=2)+'\n');save()
    except Exception as e:
        result.update(status='timeout' if isinstance(e,(TimeoutError,subprocess.TimeoutExpired)) else 'failed',error=str(e));save();raise
    print(json.dumps(result,indent=2))


if __name__=='__main__':main()
