"""Re-export frozen feasible implementations with complete native IO STA.

This checks backend compatibility only. It does not apply a new structural
heuristic and cannot establish that heuristic's generalization or timing gain.
Source route audit files are hash-checked to detect replay side effects.
"""
import argparse,json,subprocess,sys,time
from pathlib import Path
from ownpr_device import ROOT
from ownpr_image_storage import load_json,sha,compact_json,compact,compact_text
from workspace_budget import reserve


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--out',type=Path,required=True);p.add_argument('--deadline-epoch',type=float,required=True)
    p.add_argument('--policy',type=Path,default=ROOT/'results/vexriscv_110_evaluation_policy_001.json');a=p.parse_args()
    reserve(ROOT,2_000_000);a.out.mkdir(exist_ok=False);policy=load_json(a.policy)
    result=dict(scope=__doc__,status='running',structural_algorithm_applied=False,new_algorithm_generalization_proven=False,deadline_epoch=a.deadline_epoch,cases=[])
    def save():
        result['updated_epoch']=time.time();(a.out/'manifest.json').write_text(json.dumps(result,indent=2))
    save()
    for case in ('uart','int2float','tmds'):
        b=policy['regression']['baselines'][case];row=b['checkpoint'];audit=ROOT/row['selected']['routes']/'joint_route_audit.json';before=sha(audit) if audit.is_file() else None
        entry=dict(design=case,mode='frozen_implementation_backend_compatibility',baseline_mhz=b['qualified_mhz'],guard_mhz=b['guard_mhz'],raw_structural_algorithm='not_applied',runs=[]);result['cases'].append(entry);save()
        report=a.out/(case+'_source.json');report.write_text(json.dumps(dict(designs=[row]),indent=2))
        for label,target in (('baseline',b['qualified_mhz']),('guard95',b['guard_mhz'])):
            remain=int(a.deadline_epoch-time.time())-45
            if remain<150:
                entry.update(status='incomplete_deadline',accepted=False);save();break
            reserve(ROOT,1_050_000_000);out=a.out/(case+'_'+label)
            cmd=[sys.executable,str(ROOT/'scripts/replay_coopt_checkpoint.py'),'--case',case,'--report',str(report),'--out',str(out),'--target-mhz',str(target),'--seconds',str(min(350,remain))]
            with (a.out/(case+'_'+label+'.log')).open('w') as log:code=subprocess.call(cmd,cwd=ROOT,stdout=log,stderr=subprocess.STDOUT)
            run=dict(label=label,target_mhz=target,returncode=code);entry['runs'].append(run)
            if code:
                entry.update(status='replay_failed',accepted=False);save();break
            m=load_json(out/'manifest.json')['metrics'];run['metrics']=m;save()
            if m['native_constraints_satisfied'] and m['other_checks_passed'] and not m['setup_violations'] and not m['hold_violations']:
                entry.update(status='baseline_pass' if label=='baseline' else 'guard_pass_baseline_warning',accepted=True);save()
            elif m['hold_violations'] or not m['other_checks_passed']:
                entry.update(status='hold_or_other_check_failed',accepted=False);save()
            else:entry.update(status='setup_failed',accepted=False);save()
            receipts=[]
            for f in (out/'export/partial_configuration.json',out/'export/native/partial_configuration.json',out/'export/native/probe.fs',out/'export/native/probe.tr'):
                if f.is_file() and f.stat().st_size>15_000_000:
                    reserve(ROOT,f.stat().st_size+1_000_000);receipts.append((compact_json if f.suffix=='.json' else compact if f.suffix=='.fs' else compact_text)(f))
            (out/'storage_compaction.json').write_text(json.dumps(receipts,indent=2))
            if entry['accepted'] or entry['status']=='hold_or_other_check_failed':break
        after=sha(audit) if audit.is_file() else None
        entry['source_route_audit_unchanged']=before==after
        if before!=after:entry.update(status='source_side_effect',accepted=False)
        save();print(case,entry['status'],flush=True)
    result.update(status='passed' if all(c.get('accepted') for c in result['cases']) and len(result['cases'])==3 else 'failed_or_incomplete',workspace_bytes=reserve(ROOT,0)['current_bytes']);save()
    print(json.dumps({k:result[k] for k in ('status','structural_algorithm_applied','new_algorithm_generalization_proven','workspace_bytes')},indent=2))
    if result['status']!='passed':raise SystemExit(1)


if __name__=='__main__':main()
