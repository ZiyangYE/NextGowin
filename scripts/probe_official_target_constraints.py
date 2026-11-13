"""Check whether less aggressive target-relative constraints resolve vendor hold.

Inputs are provisional scouting references, never accepted baselines. This
executes explicitly labelled official P&R and cannot create own results.
"""
import argparse,json,re,subprocess,sys,time
from pathlib import Path
from ownpr_device import ROOT
from ownpr_image_storage import load_json,sha
from ownpr_frequency_policy import target_evaluation_mhz
from project_timing import audit
from workspace_budget import reserve
from run_ownpr_pin_closure import stop_owned_process_tree


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('catalog','budget','out'):p.add_argument('--'+name,type=Path,required=True)
    a=p.parse_args();budget=load_json(a.budget);reserve(ROOT,150_000_000);a.out.mkdir(exist_ok=False)
    record=dict(status='running',role='official_pnr_constraint_calibration',baseline_frozen=False,
        reference_frequency_status='unaccepted_scouting_values_only',own_pnr_result=False,
        start_epoch=budget['start_epoch'],deadline_epoch=budget['deadline_epoch'],cases=[])
    def save():
        record.update(elapsed_seconds=time.time()-budget['start_epoch'],within_original_deadline=time.time()<budget['deadline_epoch'])
        (a.out/'manifest.json').write_text(json.dumps(record,indent=2))
    save()
    for entry in load_json(a.catalog)['designs']:
        reserve(ROOT,150_000_000);out=a.out/entry['design'];out.mkdir()
        row=dict(design=entry['design'],baseline_frozen=False);record['cases'].append(row);save()
        try:
            evaluation=target_evaluation_mhz(entry['provisional_reported_fmax_mhz']);period=1000/evaluation
            original=(ROOT/entry['sdc']).read_text()
            text,count=re.subn(r'(create_clock\s+-name\s+\S+\s+-period\s+)[\d.]+',lambda m:m[1]+format(period,'.14g'),original)
            if count!=1:raise ValueError('Expected one primary clock')
            sdc=out/'clock.sdc';sdc.write_text(text)
            command=[sys.executable,str(ROOT/'scripts/run_gowin_netlist.py'),str(ROOT/entry['netlist']),'--sdc',str(sdc),'--cst',str(ROOT/entry['cst']),'--out',str(out/'pnr'),'--timeout','240']
            remaining=budget['deadline_epoch']-time.time()
            if remaining<280:raise TimeoutError('Original complete scouting deadline')
            with (out/'runner.log').open('x') as log:
                proc=subprocess.Popen(command,cwd=ROOT,stdout=log,stderr=subprocess.STDOUT,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
                try:code=proc.wait(timeout=min(270,remaining-30))
                except subprocess.TimeoutExpired:stop_owned_process_tree(proc);raise TimeoutError('Official target-relative probe')
            if code:raise ValueError('Native P&R returned '+str(code))
            report=audit(out/'pnr',dict(part='GW5AST-LV138FPG676AC1/I0',clock_periods_ns={'clk':period}))
            (out/'physical_audit.json').write_text(json.dumps(report,indent=2))
            assert sha(out/'pnr/project.cst')==sha(ROOT/entry['cst'])
            row.update(status='observed_not_frozen',provisional_reference_mhz=entry['provisional_reported_fmax_mhz'],
                evaluation_mhz=evaluation,reported_fmax_mhz=report['frequency_reports']['clk']['reported_fmax_mhz'],
                violated_endpoints=report['violated_endpoints'],worst_reported_slack=report['worst_reported_slack_by_capture_clock'],
                hold_passed=report['violated_endpoints']['Hold']==0,physical_audit_sha256=sha(out/'physical_audit.json'),
                cst_sha256=sha(out/'pnr/project.cst'),source_netlist_sha256=sha(ROOT/entry['netlist']))
        except Exception as error:row.update(status='failed',error=repr(error))
        save()
    record['status']='observed_no_accepted_fair_baselines';record['workspace']=reserve(ROOT,0);save()
    print(json.dumps(record,indent=2))


if __name__=='__main__':main()
