"""Replay a separate definedness lemma and the unchanged joint controller goal."""
import argparse,json,sys,time,shutil,gzip
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from yosys_runtime import run,quote
from formal_defined_state import propose,add_assertion
from ownpr_image_storage import sha,load_json,copy_original,resolve
from workspace_budget import reserve


def main():
    p=argparse.ArgumentParser();p.add_argument('--parent',type=Path,required=True);p.add_argument('--budget',type=Path,required=True);p.add_argument('--out',type=Path,required=True);p.add_argument('--seconds',type=int,default=360);a=p.parse_args()
    parent=a.parent.resolve();source=parent/'product.il'
    # Compressed inputs require room for materialization as well as derived views.
    actual=resolve(source);decoded_bytes=actual.stat().st_size
    if actual.suffix=='.gz':
        with gzip.open(actual,'rb') as stream:decoded_bytes=sum(len(chunk) for chunk in iter(lambda:stream.read(1<<20),b''))
    estimated=6*decoded_bytes+100_000_000
    reserve(ROOT,estimated);out=a.out.resolve();out.mkdir(exist_ok=False);start=time.time();budget=load_json(a.budget);deadline=min(start+a.seconds,budget['deadline_epoch']-180)
    if not load_json(parent/'manifest.json').get('initial_base_passed'):raise ValueError('Parent original-initial-state base not passed')
    r=dict(status='building_defined_product',scope='Conditional controller only; full array/source composition remains separate',source_product_sha256=sha(source),parent_manifest_sha256=sha(parent/'manifest.json'),original_budget=budget,definedness_proved=False,controller_proved=False,full_source_proved=False,phases=[])
    def save():r['wall_seconds']=time.time()-start;(out/'manifest.json').write_text(json.dumps(r,indent=2))
    def phase(label,commands,seconds):
        remaining=min(seconds,deadline-time.time())
        if remaining<5:raise TimeoutError('Original budget')
        invocation=run(commands,out,label,remaining);r['phases'].append(dict(label=label,invocation=invocation));save()
    save();shutil.copyfile(__file__,out/'runner_source.py.txt')
    try:
        copy_original(source,out/'source.il')
        phase('convert',['read_rtlil '+quote(out/'source.il'),'clean -purge','check -assert','write_json '+quote(out/'product.json')],40)
        d=load_json(out/'product.json');candidates=propose(d['modules']['product']);(out/'candidates.json').write_text(json.dumps(candidates,indent=2));r['defined_candidate_count']=len(candidates['candidate_bits'])
        augmented=add_assertion(d,candidates['candidate_bits']);(out/'with_definedness.json').write_text(json.dumps(augmented,separators=(',',':')))
        m=augmented['modules']['product'];excluded=[n for n,c in m['cells'].items() if c['type']=='$assert' and n!='$ng_defined$assert'];r['separate_lemma_excludes_nonfunctional_assertions']=excluded
        for n in excluded:del m['cells'][n]
        (out/'definedness_only.json').write_text(json.dumps(augmented,separators=(',',':')));save()
        phase('defined_initial',['read_json '+quote(out/'definedness_only.json'),'hierarchy -top product','check -assert','sat -seq 1 -set-init-undef -set-def-inputs -prove-asserts -verify'],40)
        phase('defined_induction',['read_json '+quote(out/'definedness_only.json'),'hierarchy -top product','check -assert','sat -seq 1 -tempinduct -maxsteps 1 -set-init-undef -set-def-inputs -prove-asserts -verify'],90)
        r['definedness_proved']=True;save()
        phase('joint_induction',['read_json '+quote(out/'with_definedness.json'),'hierarchy -top product','check -assert','sat -seq 4 -tempinduct -maxsteps 4 -set-init-undef -set-def-inputs -prove-asserts -show-ports -dump_vcd '+quote(out/'joint_counterexample.vcd')+' -verify'],180)
        r.update(status='conditional_controller_proved',controller_proved=True)
    except Exception as e:r.update(status='proof_rejected_or_incomplete',error=repr(e));save();raise
    save()


if __name__=='__main__':main()
