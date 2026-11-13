"""Run conditional controller SEC with every memory command exposed as an output.

This is not a production source-proof certificate: storage transition/composition
obligations and the exact SDK model admission remain separate required gates.
"""
import argparse,json,sys,time,shutil
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from ownpr_image_storage import load_json,sha
from compositional_memory_cut import propose,initial_relation,abstract
from yosys_runtime import run,quote
from workspace_budget import reserve


def main():
    p=argparse.ArgumentParser();p.add_argument('--pair',type=Path,required=True);p.add_argument('--budget',type=Path,required=True);p.add_argument('--out',type=Path,required=True)
    p.add_argument('--swap-replicas',action='store_true');p.add_argument('--swap-halves',action='store_true');p.add_argument('--seconds',type=int,default=180);a=p.parse_args()
    reserve(ROOT,250_000_000);start=time.time();out=a.out.resolve();out.mkdir(exist_ok=False);pair=a.pair.resolve();meta=load_json(pair/'manifest.json');budget=load_json(a.budget)
    if meta['status']!='array_transition_systems_built_not_proved':raise ValueError('Unverified transition-system build')
    for role in ('gold','gate'):
        if sha(pair/(role+'_memory.json'))!=meta[role]['sha256']:raise ValueError('Array transition system changed')
    gold=load_json(pair/'gold_memory.json');gate=load_json(pair/'gate_memory.json');top=meta['top'];bridge=load_json(pair/'bridge.json')
    groups=propose(gold,gate,top,bridge,a.swap_replicas,a.swap_halves);initial=initial_relation(gold,gate,top,groups)
    r=dict(status='preparing_conditional_controller',full_source_equivalence_proved=False,production_model_admission=False,groups=groups,initial_storage_relation=initial,
        source_sha256={str(pair/n):sha(pair/n) for n in ('manifest.json','gold_memory.json','gate_memory.json','bridge.json')},original_budget=budget,
        unproved_obligations=['exact SDK SDPB model admission','well-founded array/controller composition','complete original-pair initialization composition','physical/configuration equivalence'])
    def save():r['wall_seconds']=time.time()-start;(out/'manifest.json').write_text(json.dumps(r,indent=2))
    shutil.copyfile(__file__,out/'runner_source.py.txt');shutil.copyfile(ROOT/'scripts/compositional_memory_cut.py',out/'cut_source.py.txt');save()
    try:
        interfaces=[]
        for role,design in [('gold',gold),('gate',gate)]:
            cut,info=abstract(design,top,groups,role);(out/(role+'_cut.json')).write_text(json.dumps(cut,separators=(',',':')))
            r[role]=dict(info,json_bytes=(out/(role+'_cut.json')).stat().st_size,sha256=sha(out/(role+'_cut.json')))
            interfaces.append({n:(v['direction'],len(v['bits'])) for n,v in cut['modules'][top]['ports'].items()})
        if interfaces[0]!=interfaces[1]:raise ValueError('Abstract interface mismatch')
        r['observed_port_count']=len(interfaces[0]);r['status']='proving_conditional_controller';save()
        commands=[]
        for role in ('gold','gate'):
            commands+=['read_json '+quote(out/(role+'_cut.json')),'hierarchy -check -top '+top,'opt_clean','check -assert',
                'clk2fflogic','opt','rename '+top+' '+role,'design -stash '+role]
        commands+=['design -copy-from gold gold','design -copy-from gate gate','design -save original_controller_pair',
            'miter -equiv -ignore_gold_x -make_assert -make_outputs -flatten gold gate original_base','hierarchy -check -top original_base','opt',
            'sat -seq 4 -set-init-undef -set-def-inputs -prove-asserts -show-ports -dump_vcd '+quote(out/'initial_counterexample.vcd')+' -verify',
            'design -load original_controller_pair','equiv_make gold gate equiv','hierarchy -check -top equiv','design -save induction_input',
            'equiv_miter -assert -undef proof_base','hierarchy -check -top proof_base','flatten','opt',
            'sat -seq 4 -set-init-undef -set-def-inputs -prove-asserts -verify','design -load induction_input','hierarchy -check -top equiv',
            'equiv_simple -seq 4','equiv_induct -seq 4','equiv_status -assert']
        remaining=min(a.seconds,budget['deadline_epoch']-time.time()-90)
        if remaining<20:raise TimeoutError('Original deadline')
        r['invocation']=run(commands,out,'proof',remaining)
        log=(out/'proof.log').read_text()
        if 'Equivalence successfully proven!' not in log:raise ValueError('No completed equivalence certificate')
        r.update(status='conditional_controller_and_commands_proved',original_controller_initial_steps=4,induction_steps=4,
            scope='All original IO and every normalized memory command, under common raw-read oracle inputs. Not a complete memory/source proof.')
    except Exception as error:r.update(status='conditional_proof_rejected_or_incomplete',error=repr(error));save();raise
    save();print(json.dumps(dict(status=r['status'],wall_seconds=r['wall_seconds']),indent=2))


if __name__=='__main__':main()
