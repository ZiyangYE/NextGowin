"""Qualify raw physical-cone guard decisions against complete actual STA."""
import argparse,json,subprocess,sys,time
from pathlib import Path
from ownpr_device import ROOT
from ownpr_image_storage import load_json,sha,compact_json,compact,compact_text
from workspace_budget import reserve
from run_ownpr_pin_closure import metric,stop_owned_process_tree


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--budget',type=Path,required=True);p.add_argument('--out',type=Path,required=True);p.add_argument('--audit-name',default='native_guard_audit.json');a=p.parse_args()
    a.out=a.out.resolve();budget=load_json(a.budget);report=load_json(a.out/'manifest.json');reserve(ROOT,3_000_000)
    if Path(a.audit_name).name!=a.audit_name or not a.audit_name.endswith('.json'):raise ValueError('Invalid audit filename')
    audit_file=a.out/a.audit_name
    if audit_file.exists():raise FileExistsError(audit_file)
    policy=load_json(ROOT/'results/vexriscv_110_evaluation_policy_001.json')
    if report['status']!='raw_searches_finished_native_audit_pending':raise ValueError('Raw searches incomplete')
    audit=dict(status='validating',cases=[],new_operator_native_generalization_proven=False,new_operator_performance_generalization_proven=False,
               scope='Safety on three frozen regression guards at their baseline frequencies; does not prove general performance improvement',deadline_epoch=budget['deadline_epoch'])
    def remain():return budget['deadline_epoch']-time.time()
    def save():
        audit.update(attempt_elapsed_seconds=time.time()-budget['start_epoch'],within_original_deadline=remain()>0);audit_file.write_text(json.dumps(audit,indent=2))
    def run(label,script,args,cap):
        command=[sys.executable,str(ROOT/script),*map(str,args)]
        with (a.out/(label+'.log')).open('x') as log:
            proc=subprocess.Popen(command,cwd=ROOT,stdout=log,stderr=subprocess.STDOUT,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            try:code=proc.wait(timeout=min(cap,max(1,remain()-45)))
            except subprocess.TimeoutExpired:stop_owned_process_tree(proc);raise TimeoutError('Original guard deadline during '+label)
        if code:raise ValueError('Failed '+label)
    save()
    for case in report['cases']:
        name=case['design'];source=case['source_checkpoint'];baseline_mhz=policy['regression']['baselines'][name]['qualified_mhz'];row=dict(design=name,raw_status=case['status'],qualified=False,fallback_used=False)
        audit['cases'].append(row);save()
        try:
            if case['returncode'] or case['status'] not in ('unchanged_raw_decision','changed_raw_candidate_requires_native_sta'):raise ValueError('No successful raw decision')
            for path,h in source['selected_source_sha256'].items():
                if sha(ROOT/path)!=h:raise ValueError('Frozen guard changed')
            search=ROOT/case['search'];sm=load_json(search/'manifest.json');model=ROOT/case['model']
            for key,path in [('source_netlist_sha256',ROOT/source['selected']['placement']/'packed.json'),('source_placement_sha256',ROOT/source['selected']['placement']/'placement.json'),('source_routes_sha256',ROOT/source['selected']['routes']/'routes.json'),('source_model_sha256',model/'model.json')]:
                if sm[key]!=sha(path):raise ValueError('Raw search source mismatch')
            if not sm['measured_candidates']:raise ValueError('No physically measured candidate')
            if case['status']=='unchanged_raw_decision':
                native=ROOT/source['selected']['sta'];nm=load_json(native/'manifest.json');observed=metric(native)
                if nm['packed_sha256']!=sm['source_netlist_sha256'] or nm['placement_sha256']!=sm['source_placement_sha256'] or nm['routes_sha256']!=sm['source_routes_sha256']:raise ValueError('Unchanged raw output not bound to original native STA')
                row.update(native_sta_reused_for_identical_raw_output=True,qualification_mhz=baseline_mhz,native=str(native.relative_to(ROOT)),metrics=observed)
            else:
                if remain()<310:raise TimeoutError('Insufficient original budget for changed guard signoff')
                raw={k:ROOT/v for k,v in case['raw_output'].items()};export=a.out/(name+'_export');contract={k:ROOT/v for k,v in source['constraints'].items()};clock=ROOT/source['selected']['clock_capture']
                run(name+'_export','scripts/export_ownpr.py',['--placement',raw['placement'],'--routes',raw['routes'],'--clock-capture',clock,'--cst',contract['cst'],'--sdc',contract['sdc'],'--io-reference',contract['io_reference'],'--seconds',int(min(260,remain()-100)),'--out',export],280)
                ref=a.out/(name+'_source_reference.json');ref.write_text(json.dumps(dict(designs=[source]),indent=2));rebuilt=a.out/(name+'_rebuilt');validation=a.out/(name+'_native_model_validation.json')
                evaluator='scripts/evaluate_wide_mapping_graph.py' if sm.get('wide_mapping') else 'scripts/evaluate_shared_dag_graph.py' if sm.get('macro_input_isolation') else 'scripts/evaluate_joint_lut_graph.py'
                run(name+'_rebuild',evaluator,['--reference',ref,'--model',model,'--placement',raw['placement'],'--routes',raw['routes'],'--wire',export/'wire','--out',rebuilt],100)
                run(name+'_compare','scripts/compare_bidir_native_endpoints.py',['--model',model,'--placement',raw['placement'],'--routes',raw['routes'],'--wire',export/'wire','--native',export/'native','--rebuilt-model',rebuilt,'--out',validation],80)
                observed=load_json(export/'manifest.json')['metrics'];row.update(native_sta_reused_for_identical_raw_output=False,qualification_mhz=baseline_mhz,native=str((export/'native').relative_to(ROOT)),metrics=observed,model_validation=load_json(validation)['checks'])
                receipts=[]
                for f in list(export.rglob('*.json'))+[export/'native/probe.fs',export/'native/probe.tr',rebuilt/'model.json']:
                    if f.is_file() and f.stat().st_size>15_000_000:
                        reserve(ROOT,f.stat().st_size+1_000_000);receipts.append((compact_json if f.suffix=='.json' else compact if f.suffix=='.fs' else compact_text)(f))
                (a.out/(name+'_compaction.json')).write_text(json.dumps(receipts,indent=2))
            row['qualified']=observed['native_constraints_satisfied'] and observed['other_checks_passed'] and observed['setup_violations']==observed['hold_violations']==0
            row.update(status='passed' if row['qualified'] else 'failed_actual_sta',measured_candidates=sm['measured_candidates'],candidate_errors=sm['candidate_errors'])
        except Exception as error:row.update(status='unverified',error=str(error))
        save()
    audit['new_operator_native_generalization_proven']=len(audit['cases'])==3 and all(r['qualified'] for r in audit['cases'])
    audit['status']='three_raw_guards_passed_actual_sta' if audit['new_operator_native_generalization_proven'] else 'guard_validation_incomplete_or_failed';audit['workspace_bytes']=reserve(ROOT,0)['current_bytes'];save()
    print(json.dumps(dict(status=audit['status'],cases=[{k:r.get(k) for k in ('design','status','qualification_mhz','metrics')} for r in audit['cases']]),indent=2))


if __name__=='__main__':main()
