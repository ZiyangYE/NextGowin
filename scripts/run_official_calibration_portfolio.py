"""Predeclared common vendor implementation portfolio; never freezes a baseline.

Same checked mapped netlists, pins and I/O budgets; 100/150/200 MHz physical
implementation constraints. Every candidate receives its own 1.224*Fmax
fixed-layout STA. Pick by passing non-setup checks, with complete source,
endpoint and configuration acceptance still required before freezing.
"""
import argparse,json,math,re,subprocess,sys,time,shutil
from pathlib import Path
from workspace_budget import reserve
from ownpr_image_storage import load_json,sha,_compact
from project_timing import parse
from run_ownpr_pin_closure import stop_owned_process_tree
ROOT=Path(__file__).resolve().parents[1]
IMPLEMENTATION_MHZ=(100.,150.,200.)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('catalog','budget','out'):p.add_argument('--'+name,type=Path,required=True)
    p.add_argument('--implementation-mhz',type=float,nargs='+',default=IMPLEMENTATION_MHZ)
    p.add_argument('--resume',action='store_true',help='Continue only unstarted cases with exact original catalog, policy and deadline')
    a=p.parse_args();reserve(ROOT,600_000_000);a.out=a.out.resolve()
    frequencies=tuple(sorted(set(a.implementation_mhz)))
    if not frequencies or any(not math.isfinite(f) or f<=0 or f>1000 for f in frequencies):raise ValueError('Invalid common frequency portfolio')
    budget=load_json(a.budget);catalog=load_json(a.catalog)
    if a.resume:
        result=load_json(a.out/'manifest.json')
        if result['status']!='running' or result['original_budget']!=budget or result['catalog_sha256']!=sha(a.catalog) or tuple(result['implementation_constraints_mhz'])!=frequencies:raise ValueError('Resume differs from original portfolio')
        if any(r['status']=='running' for r in result['cases']):raise ValueError('Resolve existing case process before resuming')
        for row in result['cases']:
            if row.get('result_sha256') and sha(a.out/row['case']/'reload_result.json')!=row['result_sha256']:raise ValueError('Completed case changed')
        result.setdefault('resumes',[]).append(dict(epoch=time.time(),reason='Capacity guard stopped before the next case; retained original deadline and completed cases'))
        shutil.copyfile(__file__,a.out/('resume_runner_source_'+str(len(result['resumes']))+'.py.txt'))
    else:
        a.out.mkdir(exist_ok=False);shutil.copyfile(__file__,a.out/'runner_source.py.txt')
        result=dict(status='running',role='official_common_portfolio_calibration',baseline_frozen=False,own_pnr_result=False,
        original_budget=budget,catalog_sha256=sha(a.catalog),implementation_constraints_mhz=frequencies,
        common_policy=dict(io_register_policy='core_inputs',normalization_engine='recorded_controls',place_option=3,route_option=2,correct_hold=1,route_maxfan=23),
            selected={},cases=[],cache_cleanup=[])
    def save():
        result['elapsed_seconds']=time.time()-budget['start_epoch'];result['within_original_deadline']=time.time()<budget['deadline_epoch']
        (a.out/'manifest.json').write_text(json.dumps(result,indent=2))
    def clear_cache(folder):
        folder=folder.resolve()
        if not folder.is_relative_to(a.out):raise ValueError('Cache outside portfolio')
        file=folder/'probe.pr'
        if file.is_file():
            row=dict(path=str(file.relative_to(ROOT)),bytes=file.stat().st_size,sha256=sha(file),removed=False,
                reason='Terminal non-selected native serialization cache; source/constraints/STA/physical snapshots retained, deleted bytes not directly verifiable')
            result['cache_cleanup'].append(row);save()
            if not file.resolve().is_relative_to(folder) or file.is_symlink():raise ValueError('Unsafe cache target')
            file.unlink();row['removed']=True;save()
    save()
    for entry in catalog['designs']:
        design=entry['design']
        if not re.fullmatch(r'[A-Za-z0-9_]+',design):raise ValueError('Invalid catalog identifier')
        for mhz in frequencies:
            if any(r['design']==design and r['implementation_mhz']==mhz for r in result['cases']):continue
            remaining=budget['deadline_epoch']-time.time()
            if remaining<430:raise TimeoutError('Original portfolio deadline; no reset')
            reserve(ROOT,600_000_000);folder=a.out/(design+'_'+str(int(mhz)))
            args=[sys.executable,str(ROOT/'experiments/native_fixed_layout_sta.py'),
                '--netlist',str(ROOT/entry['netlist']),'--cst',str(ROOT/entry['cst']),'--sdc',str(ROOT/entry['sdc']),
                '--budget',str(a.budget.resolve()),'--out',str(folder),'--initial-period',format(1000/mhz,'.14g'),
                '--io-register-policy','core_inputs','--restore-native-inverters','--normalization-engine','recorded_controls']
            row=dict(design=design,implementation_mhz=mhz,case=folder.name,status='running',baseline_frozen=False,command=args)
            result['cases'].append(row);save()
            with (a.out/(folder.name+'.log')).open('x') as log:
                proc=subprocess.Popen(args,cwd=ROOT,stdout=log,stderr=subprocess.STDOUT,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
                try:code=proc.wait(timeout=min(520,remaining-60))
                except subprocess.TimeoutExpired:
                    stop_owned_process_tree(proc);row.update(status='timeout',timing_eligible=False);save();continue
            row['returncode']=code
            try:
                if code:raise ValueError('Native case failed')
                meta=load_json(folder/'manifest.json');r=load_json(folder/'reload_result.json');norm=load_json(folder/'inverter_normalization.json')
                if meta['status']!='fixed_layout_sta_calibrated' or r['status']!='fixed_layout_sta_calibrated':raise ValueError('Not calibrated')
                if not meta['input_copy_unchanged'] or not meta['cst_copy_unchanged'] or norm['errors'] or not all(norm['physical_state_checks'].values()):raise ValueError('Changed implementation or unproved normalization')
                sdc=(folder/'target_relative.sdc').read_text();period=float(re.search(r'-period\s+([\d.]+)',sdc)[1])
                timing=parse((folder/'target_relative.tr').read_text(),sdc,dict(part='GW5AST-LV138FPG676AC1/I0',clock_periods_ns={'clk':period}))
                other=[v for v in timing['violations'] if v.get('kind')!='endpoint_summary' and v.get('analysis')!='setup']
                hold=timing['violated_endpoints']['Hold']==0
                stable=math.isclose(r['final_reported_fmax_mhz'],r['provisional_reference_mhz'],abs_tol=.00051,rel_tol=0)
                eligible=hold and not other and stable and r['normalization_semantics_verified'] and not r['observation_only']
                row.update(status='calibrated_not_frozen',timing_eligible=eligible,reported_fmax_mhz=r['final_reported_fmax_mhz'],
                    sta_evaluation_mhz=r['sta_evaluation_mhz'],hold_passed=hold,other_checks_passed=not other,reference_fmax_stable=stable,
                    worst_reported_slack=timing['worst_reported_slack_by_capture_clock'],actual_constraint_closed=timing['timing_closed'],
                    unresolved_gates=['source_equivalence','independent_complete_io_endpoint_and_physical_configuration_audit'],
                    result_sha256=sha(folder/'reload_result.json'))
            except Exception as error:row.update(status='rejected',timing_eligible=False,error=repr(error))
            old=result['selected'].get(design)
            if row['timing_eligible'] and (old is None or row['reported_fmax_mhz']>old['reported_fmax_mhz']):
                result['selected'][design]={k:row[k] for k in ('case','reported_fmax_mhz','implementation_mhz','sta_evaluation_mhz')};save()
                if old:clear_cache(a.out/old['case'])
            else:clear_cache(folder)
            # Presentation duplicates and non-selected large reports retain
            # exact decoded bytes. Native/source artifacts stay in this case.
            selected=result['selected'].get(design,{}).get('case')==folder.name
            for file in sorted(folder.iterdir()):
                if file.is_file() and file.stat().st_size>1_000_000 and (file.suffix=='.html' or (not selected and file.suffix in ('.json','.tr'))):
                    reserve(ROOT,file.stat().st_size+100000);result['cache_cleanup'].append(_compact(file,file.suffix));save()
            save()
    result.update(status='portfolio_completed_no_frozen_baselines',workspace=reserve(ROOT,0));save()
    print(json.dumps(dict(status=result['status'],selected=result['selected'],seconds=result['elapsed_seconds'],workspace=result['workspace']),indent=2))


if __name__=='__main__':main()
