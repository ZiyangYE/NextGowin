"""Run the same physical-cone operator on frozen frequency regression guards.

Search decisions and native-STA qualification are separate. No-change is raw
output only after a successful search, never substitution after search failure.
"""
import argparse,json,subprocess,sys,time,shutil
from pathlib import Path
from ownpr_device import ROOT
from ownpr_image_storage import load_json,sha
from workspace_budget import reserve
from run_ownpr_pin_closure import stop_owned_process_tree


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--budget',type=Path,required=True);p.add_argument('--out',type=Path,required=True)
    p.add_argument('--shared-dag',action='store_true');p.add_argument('--macro-input-isolation',action='store_true');p.add_argument('--reuse-clone-branches',action='store_true');p.add_argument('--wide-mapping',action='store_true');a=p.parse_args()
    budget=load_json(a.budget);reserve(ROOT,400_000_000);a.out.mkdir(exist_ok=False)
    shutil.copyfile(__file__,a.out/'runner_source.py.txt')
    policy=load_json(ROOT/'results/vexriscv_110_evaluation_policy_001.json')
    record=dict(status='searching',cases=[],new_operator_native_generalization_proven=False,original_deadline_epoch=budget['deadline_epoch'])
    def save():(a.out/'manifest.json').write_text(json.dumps(record,indent=2))
    save()
    for name in ('uart','int2float','tmds'):
        base=policy['regression']['baselines'][name];checkpoint=base['checkpoint']
        for path,h in checkpoint['selected_source_sha256'].items():
            if sha(ROOT/path)!=h:raise ValueError('Frozen guard source changed')
        if time.time()+200>budget['search_finish_epoch']:raise TimeoutError('Original search stop time reached')
        source={k:ROOT/v for k,v in checkpoint['selected'].items()};model=ROOT/'results/joint_lut_structural_guards_001'/(name+'_model_002');search=a.out/(name+'_search')
        cmd=[sys.executable,str(ROOT/'scripts/ownpr_joint_lut_search.py'),'--placement',str(source['placement']),'--routes',str(source['routes']),'--wire',str(source['wire']),'--model',str(model),'--operator','cone','--trials','4','--rounds','1','--per-round','4','--seconds','120','--critical-window','2','--out',str(search)]
        if a.shared_dag:cmd.append('--shared-dag')
        if a.macro_input_isolation:cmd.append('--macro-input-isolation')
        if a.reuse_clone_branches:cmd.append('--reuse-clone-branches')
        if a.wide_mapping:cmd.append('--wide-mapping')
        row=dict(design=name,source_checkpoint=checkpoint,model=str(model.relative_to(ROOT)),search=str(search.resolve().relative_to(ROOT)),command=cmd,qualified=False,raw_output=None);record['cases'].append(row);save()
        with (a.out/(name+'.log')).open('x') as log:
            process=subprocess.Popen(cmd,cwd=ROOT,stdout=log,stderr=subprocess.STDOUT,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            try:code=process.wait(timeout=165)
            except subprocess.TimeoutExpired:stop_owned_process_tree(process);row.update(status='search_timeout');save();continue
        row['returncode']=code
        if code:row.update(status='search_failed');save();continue
        result=load_json(search/'manifest.json');row.update(search_status=result['status'],measured_candidates=result['measured_candidates'],candidate_errors=result['candidate_errors'],operator_sources=result['source_sha256'])
        if result['status']=='routed':row.update(status='changed_raw_candidate_requires_native_sta',raw_output=dict(placement=str((search/'placement').resolve().relative_to(ROOT)),routes=str(search.resolve().relative_to(ROOT))))
        elif result['status']=='no_admissible_joint_lut_candidate':row.update(status='unchanged_raw_decision',raw_output={k:v for k,v in checkpoint['selected'].items() if k in ('placement','routes')},byte_identical_to_frozen_source=True)
        else:row.update(status='unexpected_search_status')
        save()
    record['status']='raw_searches_finished_native_audit_pending';record['workspace_bytes']=reserve(ROOT,0)['current_bytes'];save()
    print(json.dumps(dict(status=record['status'],cases=[{k:r.get(k) for k in ('design','status','measured_candidates','candidate_errors')} for r in record['cases']]),indent=2))


if __name__=='__main__':main()
