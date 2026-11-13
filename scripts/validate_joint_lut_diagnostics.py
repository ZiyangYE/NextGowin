"""Exercise changed structural candidates without adopting them as guard output."""
import argparse,json,subprocess,sys,time
from pathlib import Path
from ownpr_device import ROOT
from ownpr_image_storage import load_json,sha,compact_json,compact,compact_text
from workspace_budget import reserve
from run_ownpr_pin_closure import stop_owned_process_tree


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--out',type=Path,required=True);a=p.parse_args();out=a.out.resolve();budget=load_json(out/'budget.json');result=dict(status='running',diagnostic_only=True,not_delivered=True,cases=[])
    def remain():return budget['deadline_epoch']-time.time()
    def save():
        result.update(total_attempt_elapsed_seconds=time.time()-budget['start_epoch'],within_original_deadline=remain()>0);(out/'diagnostic_validation.json').write_text(json.dumps(result,indent=2))
    def run(label,script,args):
        if remain()<120:raise TimeoutError('Original complete guard deadline')
        cmd=[sys.executable,str(ROOT/script),*map(str,args)];stamp=time.time()
        with (out/(label+'.log')).open('x') as log:
            process=subprocess.Popen(cmd,cwd=ROOT,stdout=log,stderr=subprocess.STDOUT,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            try:code=process.wait(timeout=min(300,remain()-60))
            except subprocess.TimeoutExpired:stop_owned_process_tree(process);raise TimeoutError('Diagnostic deadline during '+label)
        if code:raise ValueError('Diagnostic stage failed: '+label)
        return time.time()-stamp
    reserve(ROOT,3_000_000);save()
    try:
        for case in ('uart','int2float','tmds'):
            source=out/(case+'_diagnostic_001');meta=load_json(source/'manifest.json')
            if meta['status']!='routed' or not meta['diagnostic_only']:raise ValueError('Not an isolated changed diagnostic')
            reference=out/(case+'_reference.json');row=load_json(reference)['designs'][0];state=row['selected'];contract=row['constraints'];export=out/(case+'_diagnostic_export_001');feedback=out/(case+'_diagnostic_feedback_001');model=out/(case+'_model_002')
            entry=dict(design=case,diagnostic_only=True,delivered=False,source=str(source.relative_to(ROOT)),source_sha256=sha(source/'manifest.json'));result['cases'].append(entry);save();reserve(ROOT,1_000_000_000)
            run(case+'_diagnostic_export','scripts/export_ownpr.py',['--placement',source/'placement','--routes',source,'--clock-capture',ROOT/state['clock_capture'],'--cst',ROOT/contract['cst'],'--sdc',ROOT/contract['sdc'],'--io-reference',ROOT/contract['io_reference'],'--seconds',int(min(280,remain()-120)),'--out',export])
            em=load_json(export/'manifest.json');entry['metrics']=em['metrics'];save()
            if not em['metrics']['native_constraints_satisfied'] or not em['metrics']['other_checks_passed'] or em['metrics']['setup_violations'] or em['metrics']['hold_violations']:raise ValueError('Changed diagnostic failed complete baseline STA: '+case)
            run(case+'_diagnostic_feedback','scripts/evaluate_joint_lut_graph.py',['--reference',reference,'--model',model,'--placement',source/'placement','--routes',source,'--wire',export/'wire','--out',feedback])
            validation=out/(case+'_diagnostic_native_validation.json')
            run(case+'_diagnostic_native_validation','scripts/compare_bidir_native_endpoints.py',['--model',model,'--placement',source/'placement','--routes',source,'--wire',export/'wire','--native',export/'native','--rebuilt-model',feedback,'--out',validation])
            vm=load_json(validation);entry.update(status='changed_structure_complete_sta_and_model_pass',model_validation=vm['checks'],validation_sha256=sha(validation));save()
            receipts=[]
            for folder in (export,feedback):
                for f in list(folder.rglob('*.json'))+list(folder.rglob('probe.fs'))+list(folder.rglob('probe.tr')):
                    if f.is_file() and f.stat().st_size>500_000:
                        reserve(ROOT,f.stat().st_size+1_000_000);receipts.append((compact_json if f.suffix=='.json' else compact if f.suffix=='.fs' else compact_text)(f))
            with (export/'storage_compaction.json').open('x') as stream:json.dump(receipts,stream,indent=2)
            print(case,entry['status'],flush=True)
        result.update(status='all_changed_diagnostics_validated',workspace_bytes=reserve(ROOT,0)['current_bytes']);save()
    except Exception as error:result.update(status='failed_or_incomplete',error=str(error));save();raise


if __name__=='__main__':main()
