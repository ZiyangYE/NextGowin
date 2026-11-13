"""Run labelled official SYN+PR scouting from a data-only design catalog.

Scouting is never a frozen fair baseline: final target-relative STA, source/
interface equivalence and physical/IO coverage audits are separate gates.
"""
import argparse,json,os,re,subprocess,sys,time
from pathlib import Path
from ownpr_device import ROOT
from ownpr_image_storage import load_json,sha
from project_timing import audit
from workspace_budget import reserve
from run_ownpr_pin_closure import stop_owned_process_tree


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--catalog',type=Path,required=True);p.add_argument('--out',type=Path,required=True)
    p.add_argument('--budget',type=Path,help='Retain original start/deadline during a source-repair retry')
    a=p.parse_args();catalog=load_json(a.catalog);reserve(ROOT,400_000_000)
    a.out.mkdir(exist_ok=False)
    prior=load_json(a.budget) if a.budget else None
    start=prior['start_epoch'] if prior else time.time();deadline=prior['deadline_epoch'] if prior else start+3450
    record=dict(status='running',role='official_synthesis_and_official_pnr_scout_only',
        own_pnr_result=False,baseline_frozen=False,catalog_sha256=sha(a.catalog),start_epoch=start,deadline_epoch=deadline,
        common_options=dict(scout_mhz=200.,iob_register_option_policy='no_disable_flags_native_device_defaults_apply',correct_hold=1,place_option=3,route_option=2),cases=[])
    def save():
        record.update(elapsed_seconds=time.time()-start,within_deadline=time.time()<deadline)
        (a.out/'manifest.json').write_text(json.dumps(record,indent=2))
    def run(script,args,log,cap):
        remaining=deadline-time.time()
        if remaining<60:raise TimeoutError('Original scouting cohort deadline')
        with log.open('x') as stream:
            proc=subprocess.Popen([sys.executable,str(ROOT/script),*map(str,args)],cwd=ROOT,
                env=dict(os.environ,TCL_LIBRARY='C:/Gowin/Gowin_V1.9.12.04_x64/IDE/lib/tcl8.6'),
                stdout=stream,stderr=subprocess.STDOUT,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            try:code=proc.wait(timeout=min(cap,remaining-30))
            except subprocess.TimeoutExpired:stop_owned_process_tree(proc);raise TimeoutError(script)
        if code:raise ValueError(script+' returncode='+str(code))
    save()
    for entry in catalog['designs']:
        folder=a.out/entry['design'];folder.mkdir();row=dict(design=entry['design'],baseline_frozen=False);record['cases'].append(row);save()
        try:
            reserve(ROOT,150_000_000)
            source_sdc=ROOT/entry['sdc'];text=source_sdc.read_text()
            text,count=re.subn(r'(create_clock\s+-name\s+\S+\s+-period\s+)[\d.]+',lambda m:m[1]+'5',text)
            if count!=1:raise ValueError('Expected single primary-clock scouting contract')
            sdc=folder/'scout.sdc';sdc.write_text(text)
            row.update(source_sdc_sha256=sha(source_sdc),cst_sha256=sha(ROOT/entry['cst']),frontend_manifest_sha256=sha(ROOT/entry['frontend']/'manifest.json'))
            run('scripts/native_project_synthesis.py',['--frontend',ROOT/entry['frontend'],'--sdc',sdc,'--out',folder/'synthesis'],folder/'synthesis_runner.log',240)
            run('scripts/run_gowin_netlist.py',[folder/'synthesis/mapped.vg','--sdc',sdc,'--cst',ROOT/entry['cst'],'--out',folder/'pnr','--timeout','240'],folder/'pnr_runner.log',270)
            physical=audit(folder/'pnr',dict(part='GW5AST-LV138FPG676AC1/I0',clock_periods_ns={'clk':5.}))
            (folder/'physical_audit.json').write_text(json.dumps(physical,indent=2))
            hold=physical['violated_endpoints']['Hold']==0
            other=not any(v for v in physical['violations'] if v.get('analysis')!='setup' and v.get('kind')!='endpoint_summary')
            row.update(status='scouted_requires_final_contract_and_full_audit',reported_fmax_mhz=physical['frequency_reports']['clk']['reported_fmax_mhz'],
                hold_passed=hold,other_reported_timing_checks_passed=other,actual_scout_constraint_mhz=200.,
                worst_reported_slack=physical['worst_reported_slack_by_capture_clock'],
                violated_endpoints=physical['violated_endpoints'],timing_closed_at_scout_constraint=physical['timing_closed'],
                physical_audit_sha256=sha(folder/'physical_audit.json'),official_synthesis_manifest_sha256=sha(folder/'synthesis/manifest.json'),
                next_gate='Final STA under baseline*1.20*1.02 plus equivalence/IO/physical/configuration coverage audit; not accepted frequency yet')
        except Exception as error:row.update(status='failed',error=repr(error))
        save()
    record['status']='scouting_finished_no_frozen_baselines';record['workspace']=reserve(ROOT,0);save()
    print(json.dumps(dict(status=record['status'],cases=record['cases'],elapsed_seconds=record['elapsed_seconds']),indent=2))


if __name__=='__main__':main()
