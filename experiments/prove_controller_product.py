"""Independent product-system proof; auxiliary state observers never rewrite fanouts."""
import argparse,json,sys,time,shutil
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from ownpr_image_storage import load_json,sha
from restore_equiv_miter_init import init_values
from yosys_runtime import run,quote
from workspace_budget import reserve
from audit_product_observers import audit
from formal_single_clock import audit as clock_audit, audit_normalized


def main():
    p=argparse.ArgumentParser();p.add_argument('--source',type=Path,required=True);p.add_argument('--helpers',type=Path,required=True);p.add_argument('--budget',type=Path,required=True);p.add_argument('--out',type=Path,required=True);p.add_argument('--seconds',type=int,default=360);p.add_argument('--clock-mode',choices=['sampled','edge'],default='sampled');a=p.parse_args()
    reserve(ROOT,300_000_000 if a.clock_mode=='edge' else 400_000_000);out=a.out.resolve();out.mkdir(exist_ok=False);start=time.time();budget=load_json(a.budget);deadline=min(start+a.seconds,budget['deadline_epoch']-120);helpers=load_json(a.helpers);source=a.source.resolve();parent=load_json(source/'manifest.json')
    r=dict(status='building_independent_product',clock_mode=a.clock_mode,conditional_controller_proved=False,full_source_equivalence_proved=False,production_admitted=False,helpers=helpers,original_budget=budget,source_manifest_sha256=sha(source/'manifest.json'),helper_source_sha256=sha(a.helpers),phases=[])
    def save():r['wall_seconds']=time.time()-start;(out/'manifest.json').write_text(json.dumps(r,indent=2))
    def phase(label,commands,limit):
        t=min(limit,deadline-time.time())
        if t<5:raise TimeoutError('Original proof/round deadline')
        v=run(commands,out,label,t);r['phases'].append(dict(name=label,invocation=v));save()
    shutil.copyfile(__file__,out/'runner_source.py.txt');save()
    try:
        tops=[]
        driven={}
        for role in ('gold','gate'):
            raw=load_json(source/(role+'_cut.json'))
            module=next(m for m in raw['modules'].values() if str(m.get('attributes',{}).get('top','0')).strip('0')=='1')
            drivers={b for p in module['ports'].values() if p['direction']=='input' for b in p['bits']}
            drivers.update(b for c in module['cells'].values() for pn,bs in c['connections'].items() if c['port_directions'][pn]=='output' for b in bs)
            driven[role]=drivers
        rejected=[];accepted=[]
        for i,h in enumerate(helpers):
            missing={role:[b for b in h[role] if isinstance(b,int) and b not in driven[role]] for role in ('gold','gate')}
            if any(missing.values()):rejected.append(dict(original_index=i,helper=h,undriven=missing))
            else:accepted.append(h)
        helpers=accepted;r['helpers']=helpers;r['rejected_unused_alias_candidates']=rejected;save()
        for role in ('gold','gate'):
            path=source/(role+'_cut.json')
            if sha(path)!=parent[role]['sha256']:raise ValueError('Conditional source changed')
            d=load_json(path);matches=[n for n,m in d['modules'].items() if str(m.get('attributes',{}).get('top','0')).strip('0')=='1']
            if len(matches)!=1:raise ValueError('One top required')
            top=matches[0];tops.append(top);m=d['modules'][top];initial=init_values(m)
            if a.clock_mode=='edge':r.setdefault('clock_profiles',{})[role]=clock_audit(m)
            for i,h in enumerate(helpers):
                name='ng_state_helper_'+str(i);bits=h[role]
                if name in m['ports'] or name in m['netnames']:raise ValueError('Observer namespace collision')
                m['ports'][name]=dict(direction='output',bits=bits);m['netnames'][name]=dict(hide_name=0,bits=bits,attributes={})
            if init_values(m)!=initial:raise ValueError('Helper observations changed INIT')
            r.setdefault('observer_audits',[]).append(audit(load_json(path),d,helpers,role));save()
            (out/(role+'_helpers.json')).write_text(json.dumps(d,separators=(',',':')))
        if tops[0]!=tops[1]:raise ValueError('Different tops')
        commands=[]
        for role in ('gold','gate'):
            commands+=['read_json '+quote(out/(role+'_helpers.json')),'hierarchy -check -top '+top,'check -assert']
            if a.clock_mode=='edge':commands+=['dffunmap','formalff -clk2ff','check -assert','write_json '+quote(out/(role+'_normalized.json')),'opt_clean']
            else:commands+=['clk2fflogic','opt']
            commands+=['rename '+top+' '+role,'design -stash '+role]
        commands+=['design -copy-from gold gold','design -copy-from gate gate','miter -equiv -ignore_gold_x -make_assert -make_outputs -flatten gold gate product',
            'hierarchy -check -top product','clean -purge' if a.clock_mode=='edge' else 'opt','check -assert','write_rtlil '+quote(out/'product.il')]
        phase('build',commands,90)
        if a.clock_mode=='edge':
            for role in ('gold','gate'):
                r.setdefault('normalization_audits',{})[role]=audit_normalized(load_json(out/(role+'_helpers.json'))['modules'][top],load_json(out/(role+'_normalized.json'))['modules'][top])
            save()
        phase('initial',['read_rtlil '+quote(out/'product.il'),'hierarchy -check -top product','check -assert',
            'sat -seq 4 -set-init-undef -set-def-inputs -prove-asserts -show-ports -dump_vcd '+quote(out/'initial_counterexample.vcd')+' -verify'],120)
        r['initial_base_passed']=True;save()
        phase('induction',['read_rtlil '+quote(out/'product.il'),'hierarchy -check -top product','check -assert',
            'sat -seq 4 -tempinduct -maxsteps 4 -set-init-undef -set-def-inputs -prove-asserts -show-ports -dump_vcd '+quote(out/'induction_counterexample.vcd')+' -verify'],180)
        r.update(status='conditional_product_controller_proved',conditional_controller_proved=True)
    except Exception as error:r.update(status='product_proof_rejected_or_incomplete',error=repr(error));save();raise
    save();print(json.dumps(dict(status=r['status'],wall_seconds=r['wall_seconds'])))


if __name__=='__main__':main()
