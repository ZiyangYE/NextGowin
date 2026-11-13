"""Bounded experimental controller proof with invertible observers and exact INIT transport."""
import argparse,json,sys,time,shutil
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from ownpr_image_storage import load_json,sha
from formal_observer_encoding import encode
from restore_equiv_miter_init import restore
from yosys_runtime import run,quote
from workspace_budget import reserve


def main():
    p=argparse.ArgumentParser();p.add_argument('--source',type=Path,required=True);p.add_argument('--budget',type=Path,required=True);p.add_argument('--out',type=Path,required=True);p.add_argument('--seconds',type=int,default=480);a=p.parse_args()
    reserve(ROOT,300_000_000);source=a.source.resolve();out=a.out.resolve();out.mkdir(exist_ok=False);started=time.time();budget=load_json(a.budget);deadline=min(started+a.seconds,budget['deadline_epoch']-90)
    parent=load_json(source/'manifest.json')
    # Recover the unique top instead of relying on a project-specific proof rule.
    first=load_json(source/'gold_cut.json');tops=[n for n,m in first['modules'].items() if m.get('attributes',{}).get('top') in ('1','00000000000000000000000000000001')]
    if len(tops)!=1:raise ValueError('One declared proof top required')
    top=tops[0];record=dict(status='preparing',full_source_equivalence_proved=False,storage_composition_proved=False,phases=[],original_budget=budget,parent_manifest_sha256=sha(source/'manifest.json'))
    def save():record['wall_seconds']=time.time()-started;(out/'manifest.json').write_text(json.dumps(record,indent=2))
    def phase(name,commands,limit):
        remaining=min(limit,deadline-time.time())
        if remaining<5:raise TimeoutError('Shared proof deadline')
        value=run(commands,out,name,remaining);record['phases'].append(dict(name=name,invocation=value));save()
    for f in (Path(__file__),ROOT/'scripts/formal_observer_encoding.py',ROOT/'scripts/restore_equiv_miter_init.py'):shutil.copyfile(f,out/(f.name+'.txt'))
    save()
    try:
        for role in ('gold','gate'):
            path=source/(role+'_cut.json')
            if sha(path)!=parent[role]['sha256']:raise ValueError('Conditional source changed')
            transformed,cert=encode(first if role=='gold' else load_json(path),top);(out/(role+'_observed.json')).write_text(json.dumps(transformed,separators=(',',':')));record[role]=cert
        save();commands=[]
        for role in ('gold','gate'):
            commands+=['read_json '+quote(out/(role+'_observed.json')),'hierarchy -check -top '+top,'opt_clean','check -assert','clk2fflogic','opt','rename '+top+' '+role,'design -stash '+role]
        commands+=['design -copy-from gold gold','design -copy-from gate gate','design -save original_pair',
            'miter -equiv -ignore_gold_x -make_assert -flatten gold gate original_base','hierarchy -check -top original_base','opt',
            'sat -seq 4 -set-init-undef -set-def-inputs -prove-asserts -verify','design -load original_pair',
            'equiv_make gold gate equiv','hierarchy -check -top equiv','check -assert','write_json '+quote(out/'equiv_source.json'),
            'equiv_miter -cmp -assert -undef proof_base','hierarchy -check -top proof_base','write_json '+quote(out/'matched_miter.json'),'check -assert']
        phase('original_base_and_matching',commands,150)
        restored,cert=restore(load_json(out/'equiv_source.json'),load_json(out/'matched_miter.json'));(out/'matched_initialized.json').write_text(json.dumps(restored,separators=(',',':')));record['initialization_transport']=cert;save()
        phase('matched_initial_base',['read_json '+quote(out/'matched_initialized.json'),'hierarchy -check -top proof_base','check -assert','opt',
            'sat -seq 4 -set-init-undef -set-def-inputs -prove-asserts -show-ports -dump_vcd '+quote(out/'matched_counterexample.vcd')+' -verify'],150)
        record['matched_initial_base_passed']=True;save()
        phase('induction',['read_json '+quote(out/'equiv_source.json'),'hierarchy -check -top equiv','equiv_simple -seq 4','equiv_induct -seq 4','equiv_status -assert'],180)
        record.update(status='conditional_observed_controller_proved',conditional_controller_proved=True,original_controller_initial_base_passed=True)
    except Exception as error:record.update(status='conditional_observed_proof_rejected_or_incomplete',error=repr(error));save();raise
    save();print(json.dumps(dict(status=record['status'],wall_seconds=record['wall_seconds'])))


if __name__=='__main__':main()
