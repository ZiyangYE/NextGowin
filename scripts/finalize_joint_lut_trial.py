"""Validate a topology-changing trial; default to direct 110 MHz native Fmax."""
import argparse,copy,json,math,re,shutil,subprocess,sys,time
from pathlib import Path
from ownpr_device import ROOT
from ownpr_image_storage import load_json,sha,compact_json,compact,compact_text
from workspace_budget import reserve
from run_ownpr_pin_closure import stop_owned_process_tree


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for n in ('attempt','candidate','export','budget'):p.add_argument('--'+n,type=Path,required=True)
    p.add_argument('--reference',type=Path,default=ROOT/'results/vexriscv_routed_branch_trial_001/qualified_replay_checkpoint.json')
    p.add_argument('--reference-model',type=Path,default=ROOT/'results/vexriscv_87_3_bidir_model_001')
    p.add_argument('--independent-frequency-replay',action='store_true',help='Explicit legacy reproduction only; default uses reported Fmax without a frequency replay')
    p.add_argument('--reuse-configuration',action='store_true',help='Only affects an explicitly requested legacy independent frequency replay')
    a=p.parse_args();out=a.attempt.resolve();budget=load_json(a.budget)
    def remain():return budget['deadline_epoch']-time.time()
    reserve(ROOT,3_000_000);em=load_json(a.export/'manifest.json')
    base_reference=load_json(a.reference)['designs'][0]
    summary=dict(status='validating',goal_complete=False,target_mhz=110,previous_qualified_mhz=base_reference['qualified_mhz'],stages=[],original_budget_seconds=budget['deadline_epoch']-budget['start_epoch'],
                 original_start_epoch=budget['start_epoch'],deadline_epoch=budget['deadline_epoch'],vendor_pnr_executed=False,open_chip_database_used=False,hardware_verified=False)
    def save():
        summary.update(total_attempt_elapsed_seconds=time.time()-budget['start_epoch'],complete_elapsed_upper_bound_with_prebudget_planning_seconds=time.time()-budget['start_epoch']+120,within_original_deadline=remain()>0)
        (out/'engineering_summary.json').write_text(json.dumps(summary,indent=2))
    def run(label,script,args,cap):
        if remain()<60:raise TimeoutError('Original complete attempt deadline')
        command=[sys.executable,str(ROOT/script),*map(str,args)];stamp=time.time();row=dict(stage=label,command=command);summary['stages'].append(row);save()
        with (out/(label+'.log')).open('x') as log:
            proc=subprocess.Popen(command,cwd=ROOT,stdout=log,stderr=subprocess.STDOUT,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            try:code=proc.wait(timeout=min(cap,remain()-30))
            except subprocess.TimeoutExpired:stop_owned_process_tree(proc);raise TimeoutError('Original deadline during '+label)
        row.update(returncode=code,wall_seconds=time.time()-stamp);save()
        if code:raise ValueError('Failed '+label+'; see '+label+'.log')
    save()
    try:
        if em['status']!='verified_handoff_complete' or not em['bitstream_generated'] or not em['sta_completed']:raise ValueError('No complete final STA/encoding')
        summary['metrics_110mhz']=em['metrics'];summary['source_export']=str(a.export)
        candidate_meta=load_json(a.candidate/'manifest.json')
        evaluator='scripts/evaluate_wide_mapping_graph.py' if candidate_meta.get('wide_mapping') else 'scripts/evaluate_shared_dag_graph.py' if candidate_meta.get('macro_input_isolation') else 'scripts/evaluate_joint_lut_graph.py'
        run('full_feedback',evaluator,['--reference',a.reference,'--model',a.reference_model,'--placement',a.candidate/'placement','--routes',a.candidate,'--wire',a.export/'wire','--out',out/'full_feedback_repair_001'],120)
        run('native_model_validation','scripts/compare_bidir_native_endpoints.py',['--model',a.reference_model,'--placement',a.candidate/'placement','--routes',a.candidate,'--wire',a.export/'wire','--native',a.export/'native','--rebuilt-model',out/'full_feedback_repair_001','--out',out/'native_model_validation.json'],90)
        summary['native_model_validation']=load_json(out/'native_model_validation.json')['checks']
        receipts=[]
        for parent in (a.export,out/'export'):
            for f in list(parent.rglob('*.json'))+[parent/'native/probe.fs',parent/'native/probe.tr']:
                if f.is_file() and f.stat().st_size>15_000_000:
                    reserve(ROOT,f.stat().st_size+1_000_000);receipts.append((compact_json if f.suffix=='.json' else compact if f.suffix=='.fs' else compact_text)(f))
        with (out/'signoff_compaction.json').open('x') as stream:json.dump(receipts,stream,indent=2)
        base=base_reference;row=copy.deepcopy(base)
        state=dict(placement=a.candidate/'placement',routes=a.candidate,wire=a.export/'wire',sta=a.export/'native',clock_capture=ROOT/'results/vexriscv_frontier_partition_ablation_001/clock_encoding')
        contract=dict(cst=ROOT/'results/vexriscv_evaluation_pins_001/fixed.cst',sdc=ROOT/'results/vexriscv_110_reference_001/clock.sdc',io_reference=ROOT/'results/native_vexriscv_packed_io_reference_001');metrics=em['metrics']
        rel=lambda f:Path(f).resolve().relative_to(ROOT).as_posix()
        def checkpoint():
            files=[state['placement']/'packed.json',state['placement']/'placement.json',*[state['routes']/n for n in ('routes.json','nets.json','all_nets.json','clock_tree.json','manifest.json')],*[state['sta']/n for n in ('manifest.json','probe.tr','handoff_audit.json')],state['wire']/'wire_delays.json',contract['cst'],contract['sdc'],out/'native_model_validation.json',out/'full_feedback_repair_001/manifest.json',out/'full_feedback_repair_001/model.json']
            row.update(selected={k:rel(v) for k,v in state.items()},constraints={k:rel(v) for k,v in contract.items()},metrics=metrics,selected_source_sha256={rel(f):sha(f) for f in files})
            return dict(designs=[row])
        row.update(qualified=False,qualified_mhz=None,target_mhz=110,goal_complete=False,target_achieved=False,new_algorithm_generalization_proven=False,structural_regression_scope='Not applied on guard designs in this attempt; prior backend replays do not establish new-algorithm generalization')
        row['functional_certificate_chain'][rel(a.candidate/'placement/manifest.json')]=sha(a.candidate/'placement/manifest.json')
        with (out/'replay_checkpoint.json').open('x') as stream:json.dump(checkpoint(),stream,indent=2)
        if a.independent_frequency_replay:
            target=math.floor(metrics['reported_fmax_mhz']*10)/10
            if remain()<(170 if a.reuse_configuration else 260):raise TimeoutError('No original budget for independent qualification')
            if a.reuse_configuration:
                run('qualification','scripts/qualify_fixed_configuration.py',['--source-export',a.export,'--placement',a.candidate/'placement','--routes',a.candidate,'--cst',contract['cst'],'--sdc',contract['sdc'],'--io-reference',contract['io_reference'],'--target-mhz',target,'--seconds',int(min(260,remain()-40)),'--out',out/'qualification'],min(270,remain()-30))
            else:run('qualification','scripts/replay_coopt_checkpoint.py',['--case','vexriscv','--report',out/'replay_checkpoint.json','--target-mhz',target,'--seconds',int(min(420,remain()-50)),'--out',out/'qualification'],min(430,remain()-30))
            qm=load_json(out/'qualification/manifest.json')['metrics'];summary.update(qualified_mhz=target,qualification_metrics=qm,frequency_replayed=True)
            accepted=qm['native_constraints_satisfied'] and qm['other_checks_passed'] and qm['setup_violations']==qm['hold_violations']==0
        else:
            from ownpr_frequency_policy import reported_fmax_acceptance
            # The original source SDC is separately checked by the native
            # importer and independent graph/native endpoint comparison.
            text=contract['sdc'].read_text();period=float(re.search(r'create_clock\s+-name\s+clk\s+-period\s+([\d.]+)',text)[1])
            policy=reported_fmax_acceptance(metrics,1000/period);accepted=policy['accepted'];target=metrics['reported_fmax_mhz'];qm=metrics
            summary.update(policy,qualified_mhz=target if accepted else None,qualification_metrics=None)
            row.update(frequency_acceptance_basis=policy['frequency_acceptance_basis'],frequency_replayed=False,sta_evaluation_mhz=110.,frequency_policy_sha256=sha(ROOT/'results/frequency_reporting_policy_20261112.json'))
        search=load_json(a.candidate/'manifest.json')
        model_gain=search['current_metrics']['setup']['wns']-search['baseline_metrics']['setup']['wns']
        summary.update(status=('qualified_joint_lut_candidate' if a.independent_frequency_replay else 'accepted_reported_fmax_candidate') if accepted else 'rejected',model_110mhz_wns_gain_ns=model_gain,architectural_milestone_0_5ns_achieved=model_gain>=0.5,
                       new_algorithm_generalization_proven=False,raw_generalization_status='Not applied to UART/int2float/TMDS in this attempt',fallback_generalization='Frozen qualified checkpoints retained; not evidence of raw algorithm performance')
        if accepted:
            if a.independent_frequency_replay:
                state['sta']=out/('qualification/native' if a.reuse_configuration else 'qualification/export/native');contract['sdc']=out/'qualification/clock.sdc'
            metrics=qm;row.update(qualified=True,qualified_mhz=target,target_mhz=target if a.independent_frequency_replay else 110,total_attempt_elapsed_seconds=time.time()-budget['start_epoch'])
            with (out/('qualified_replay_checkpoint.json' if a.independent_frequency_replay else 'reported_fmax_checkpoint.json')).open('x') as stream:json.dump(checkpoint(),stream,indent=2)
        sources=out/'validation_sources';sources.mkdir()
        for name in ('ownpr_lut_graph_feedback.py','ownpr_carry_feedback.py','ownpr_carry_translate.py','ownpr_path_cone.py','ownpr_shared_dag.py','ownpr_shared_feedback.py','ownpr_wide_mapping.py','ownpr_wide_mapping_feedback.py','ownpr_joint_lut_search.py','ownpr_frequency_policy.py','repair_joint_lut_ground.py','evaluate_joint_lut_graph.py','evaluate_shared_dag_graph.py','evaluate_wide_mapping_graph.py','compare_bidir_native_endpoints.py','finalize_joint_lut_trial.py','qualify_fixed_configuration.py','ownpr_alias_feedback.py'):shutil.copyfile(ROOT/'scripts'/name,sources/name)
        summary['workspace_bytes']=reserve(ROOT,0)['current_bytes'];save();print(json.dumps({k:summary[k] for k in ('status','qualified_mhz','metrics_110mhz','qualification_metrics','total_attempt_elapsed_seconds','within_original_deadline','workspace_bytes')},indent=2))
    except Exception as error:summary.update(status='failed',error=str(error));save();raise


if __name__=='__main__':main()
