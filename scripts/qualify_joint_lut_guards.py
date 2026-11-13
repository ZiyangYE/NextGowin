"""Qualify actual raw structural-search decisions, including explicit no-change.

Candidate-search execution and its native-model validation are required.
Unchanged algorithm output is distinguished from a fallback after failure.
"""
import argparse,copy,json,subprocess,sys,time
from pathlib import Path
from ownpr_device import ROOT
from ownpr_image_storage import load_json,sha,compact_json,compact,compact_text
from workspace_budget import reserve
from run_ownpr_pin_closure import stop_owned_process_tree


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--out',type=Path,required=True);a=p.parse_args();out=a.out.resolve();budget=load_json(out/'budget.json');policy=load_json(ROOT/'results/vexriscv_110_evaluation_policy_001.json')
    reserve(ROOT,3_000_000);result=dict(status='running',structural_algorithm_executed=True,new_algorithm_performance_generalization_proven=False,cases=[],deadline_epoch=budget['deadline_epoch'])
    def remain():return budget['deadline_epoch']-time.time()
    def save():
        result.update(total_attempt_elapsed_seconds=time.time()-budget['start_epoch'],within_original_deadline=remain()>0);(out/'manifest.json').write_text(json.dumps(result,indent=2))
    def run(label,script,args):
        if remain()<180:raise TimeoutError('Insufficient original complete guard budget')
        command=[sys.executable,str(ROOT/script),*map(str,args)];stamp=time.time()
        with (out/(label+'.log')).open('x') as log:
            process=subprocess.Popen(command,cwd=ROOT,stdout=log,stderr=subprocess.STDOUT,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            try:code=process.wait(timeout=min(400,remain()-60))
            except subprocess.TimeoutExpired:stop_owned_process_tree(process);raise TimeoutError('Original guard deadline during '+label)
        if code:raise ValueError('Failed '+label)
        return time.time()-stamp
    def compact_run(folder):
        receipts=[]
        for f in list(folder.rglob('*.json'))+list(folder.rglob('probe.fs'))+list(folder.rglob('probe.tr')):
            if f.is_file() and f.stat().st_size>15_000_000:
                reserve(ROOT,f.stat().st_size+1_000_000);receipts.append((compact_json if f.suffix=='.json' else compact if f.suffix=='.fs' else compact_text)(f))
        with (folder/'storage_compaction.json').open('x') as stream:json.dump(receipts,stream,indent=2)
    current_sources=None;save()
    try:
        for case in ('uart','int2float','tmds'):
            base=policy['regression']['baselines'][case];source=base['checkpoint'];search=out/(case+'_search_002');sm=load_json(search/'manifest.json');model=out/(case+'_model');mm=load_json(model/'manifest.json');trials=load_json(search/'trials.json')
            for name,h in source['selected_source_sha256'].items():
                if sha(ROOT/name)!=h:raise ValueError('Frozen source changed '+name)
            if sm['returncode'] or sm.get('diagnostic_only') or sm['status'] not in ('routed','no_admissible_joint_lut_candidate') or mm['status']!='validated_setup_hold_feedback':raise ValueError('Incomplete or diagnostic-only raw structural run')
            measured=sum('setup' in r for r in trials)
            if not measured:raise ValueError('No physically measured structural candidate for '+case)
            versions={n:sm['source_sha256'][n] for n in ('ownpr_joint_lut_search.py','ownpr_alias_feedback.py','ownpr_lut_graph_feedback.py','ownpr_model_reference.py')}
            if current_sources is None:current_sources=versions
            elif versions!=current_sources:raise ValueError('Cases ran different algorithm versions')
            row=copy.deepcopy(source);entry=dict(design=case,baseline_mhz=base['qualified_mhz'],guard_mhz=base['guard_mhz'],raw_search_status=sm['status'],search=str(search.relative_to(ROOT)),search_sha256=sha(search/'manifest.json'),model_sha256=sha(model/'model.json'),candidate_trials=len(trials),physically_measured_candidates=measured,accepted_transactions=sm['accepted_transactions'],runs=[],fallback_used=False)
            result['cases'].append(entry);save();reserve(ROOT,1_050_000_000)
            baseline_out=out/(case+'_baseline')
            if sm['status']=='routed':
                state=dict(placement=search/'placement',routes=search,clock_capture=ROOT/source['selected']['clock_capture']);contract={k:ROOT/v for k,v in source['constraints'].items()}
                entry['raw_decision']='changed_implementation';save()
                args=[]
                for n,v in state.items():args+=['--'+n.replace('_','-'),v]
                args+=['--cst',contract['cst'],'--sdc',contract['sdc'],'--io-reference',contract['io_reference'],'--seconds',int(min(350,remain()-100)),'--out',baseline_out]
                wall=run(case+'_baseline_export','scripts/export_ownpr.py',args);em=load_json(baseline_out/'manifest.json');metrics=em['metrics'];native=baseline_out/'native'
                state.update(wire=baseline_out/'wire',sta=native);row.update(selected={k:v.relative_to(ROOT).as_posix() for k,v in state.items()},metrics=metrics)
                files=[state['placement']/'packed.json',state['placement']/'placement.json',*[state['routes']/n for n in ('routes.json','nets.json','all_nets.json','clock_tree.json')],*[native/n for n in ('manifest.json','probe.tr','handoff_audit.json')],state['wire']/'wire_delays.json']
                row['selected_source_sha256']={f.relative_to(ROOT).as_posix():sha(f) for f in files}
            else:
                entry.update(raw_decision='unchanged_after_measured_search',raw_output_byte_identical_to_input=True);save()
                report=out/(case+'_raw_checkpoint.json')
                with report.open('x') as stream:json.dump(dict(designs=[row]),stream,indent=2)
                wall=run(case+'_baseline_replay','scripts/replay_coopt_checkpoint.py',['--case',case,'--report',report,'--target-mhz',base['qualified_mhz'],'--seconds',int(min(350,remain()-100)),'--out',baseline_out]);metrics=load_json(baseline_out/'manifest.json')['metrics'];native=baseline_out/'export/native'
            entry['runs'].append(dict(label='baseline',target_mhz=base['qualified_mhz'],metrics=metrics,wall_seconds=wall,native=str(native.relative_to(ROOT))));compact_run(baseline_out)
            passed=lambda m:m['native_constraints_satisfied'] and m['other_checks_passed'] and not m['setup_violations'] and not m['hold_violations']
            if passed(metrics):entry.update(status='raw_baseline_pass',accepted=True)
            elif metrics['hold_violations'] or not metrics['other_checks_passed']:entry.update(status='raw_hold_or_other_failure_requires_diagnosis',accepted=False)
            else:
                report=out/(case+'_guard_source.json')
                with report.open('x') as stream:json.dump(dict(designs=[row]),stream,indent=2)
                guard_out=out/(case+'_guard95');wall=run(case+'_guard95','scripts/replay_coopt_checkpoint.py',['--case',case,'--report',report,'--target-mhz',base['guard_mhz'],'--seconds',int(min(350,remain()-100)),'--out',guard_out]);gm=load_json(guard_out/'manifest.json')['metrics'];entry['runs'].append(dict(label='guard95',target_mhz=base['guard_mhz'],metrics=gm,wall_seconds=wall));compact_run(guard_out)
                entry.update(status='raw_guard95_pass_baseline_warning' if passed(gm) else 'raw_guard95_failure_requires_diagnosis',accepted=passed(gm))
            entry['source_artifacts_unchanged']=all(sha(ROOT/n)==h for n,h in source['selected_source_sha256'].items());save()
            if not entry['accepted'] or not entry['source_artifacts_unchanged']:raise ValueError('Raw structural guard requires bounded diagnosis: '+case)
            print(case,entry['status'],entry['raw_decision'],flush=True)
        result.update(status='passed_raw_structural_guards',algorithm_source_sha256=current_sources,scope='Same structural algorithm was executed and measured on all three frozen designs. All outputs qualified by native complete IO STA. Unchanged outputs establish guard safety, not transformed-circuit gains.',workspace_bytes=reserve(ROOT,0)['current_bytes']);save()
        print(json.dumps({k:result[k] for k in ('status','new_algorithm_performance_generalization_proven','total_attempt_elapsed_seconds','within_original_deadline','workspace_bytes')},indent=2))
    except Exception as error:result.update(status='failed_or_incomplete',error=str(error));save();raise


if __name__=='__main__':main()
