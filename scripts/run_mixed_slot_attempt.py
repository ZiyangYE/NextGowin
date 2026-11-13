"""Automatically select, route and fully audit one calibrated mixed-slot move."""
import argparse,json,shutil,subprocess,sys,time
from datetime import datetime,timezone
from pathlib import Path
from ownpr_device import ROOT
from ownpr_image_storage import load_json,sha,copy_original
from ownpr_mixed_slot_candidates import discover
from prepare_wide_ff_lut_neighbor import transform
from workspace_budget import reserve
from run_ownpr_pin_closure import metric,improves,stop_owned_process_tree


def main():
    p=argparse.ArgumentParser();p.add_argument('--report',type=Path,required=True);p.add_argument('--case',default='vexriscv');p.add_argument('--out',type=Path,required=True);p.add_argument('--pin-feedback',type=Path);a=p.parse_args()
    start=time.monotonic();deadline=start+3500;reserve(ROOT,1_600_000_000)
    row=next(r for r in load_json(a.report)['designs'] if r['design']==a.case)
    for name,h in row['selected_source_sha256'].items():
        if sha(ROOT/name)!=h:raise ValueError('Source checkpoint changed: '+name)
    state={k:ROOT/v for k,v in row['selected'].items()};contract={k:ROOT/v for k,v in row['constraints'].items()}
    out=a.out.resolve();out.mkdir(exist_ok=False);shutil.copyfile(__file__,out/'runner_source.py.txt')
    result=dict(status='running',started_utc=datetime.now(timezone.utc).isoformat(),budget_seconds=3500,source_report_sha256=sha(a.report),initial_metrics=metric(state['sta']),accepted=False,vendor_pnr_executed=False,goal_complete=False)
    helpers=('ownpr_mixed_slot_candidates.py','prepare_wide_ff_lut_neighbor.py','ownpr_wide_ff_lut_sharing.py','ownpr_wide_ff_packing.py','ownpr_wide_lut_sharing.py')
    for name in helpers:shutil.copyfile(ROOT/'scripts'/name,out/(name+'.txt'))
    result['helper_sha256']={name:sha(out/(name+'.txt')) for name in helpers}
    def save():
        result['wall_seconds']=time.monotonic()-start;(out/'manifest.json').write_text(json.dumps(result,indent=2))
    save()
    try:
        sys.path.insert(0,str(ROOT/'experiments'));from analyze_ownpr_critical_wires import analyze
        design=load_json(state['placement']/'packed.json');placed=load_json(state['placement']/'placement.json')
        feedback=a.pin_feedback.resolve() if a.pin_feedback else out/'pins'
        if not a.pin_feedback:
            reserve(ROOT,600_000_000)
            command=[sys.executable,str(ROOT/'experiments/native_pin_feedback_probe.py'),'--source',str(state['sta']),'--placement',str(state['placement']),'--out',str(feedback)]
            result['pin_capture_command']=command;save()
            with (out/'pin_capture.log').open('w') as log:
                process=subprocess.Popen(command,cwd=ROOT,stdout=log,stderr=subprocess.STDOUT,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
                try:code=process.wait(timeout=min(450,max(1,deadline-time.monotonic())))
                except subprocess.TimeoutExpired:stop_owned_process_tree(process);raise TimeoutError('Pin capture deadline')
            if code:raise RuntimeError('Pin capture failed')
        from ownpr_pin_feedback import load as load_feedback,criticalities
        budgets,info=load_feedback(feedback,state['sta']);result['pin_feedback']=info;save()
        ranking=analyze(state['sta'],state['placement'],state['routes'],128);discovery=discover(design,placed,ranking,priorities=criticalities(budgets))
        discovery['pin_feedback']=info
        (out/'discovery.json').write_text(json.dumps(discovery,indent=2))
        if not discovery['candidates']:result['status']='no_eligible_candidates';save();return
        chosen=discovery['candidates'][0];new,change,audit=transform(design,placed,chosen['lut'],chosen['root'],chosen['slot']);candidate=out/'candidate';candidate.mkdir()
        copy_original(state['placement']/'packed.json',candidate/'packed.json');(candidate/'placement.json').write_text(json.dumps(new,indent=2))
        pm=load_json(state['placement']/'manifest.json');pm.update(status='placed',netlist_sha256=sha(candidate/'packed.json'),placement_sha256=sha(candidate/'placement.json'),source_placement_sha256=sha(state['placement']/'placement.json'),mixed_wide_ff_move=change,timing_signoff=False)
        (candidate/'manifest.json').write_text(json.dumps(pm,indent=2));(candidate/'packing_audit.json').write_text(json.dumps(audit,indent=2))
        result['candidate']=chosen;result['logical_netlist_byte_identical']=sha(candidate/'packed.json')==sha(state['placement']/'packed.json')
        if not result['logical_netlist_byte_identical']:raise ValueError('Placement candidate changed netlist')
        remain=int(deadline-time.monotonic())-1
        cmd=[sys.executable,str(ROOT/'scripts/run_ownpr_checkpoint.py'),'--placement',str(candidate),'--clock-tree',str(state['routes']/'clock_tree.json'),'--clock-capture',str(state['clock_capture']),'--reuse-routes',str(state['routes']),'--seconds',str(remain),'--route-seconds','600','--out',str(out/'implementation')]
        for key in ('cst','sdc','io_reference'):cmd+=['--'+key.replace('_','-'),str(contract[key])]
        result['command']=cmd;save()
        with (out/'implementation.log').open('w') as log:
            proc=subprocess.Popen(cmd,cwd=ROOT,stdout=log,stderr=subprocess.STDOUT,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            try:rc=proc.wait(timeout=max(1,deadline-time.monotonic()))
            except subprocess.TimeoutExpired:stop_owned_process_tree(proc);raise TimeoutError('Complete mixed-slot deadline')
        if rc:raise RuntimeError('Mixed-slot implementation failed')
        observed=metric(out/'implementation/export/native');result.update(metrics=observed,accepted=improves(result['initial_metrics'],observed))
        if result['accepted']:
            implementation=load_json(out/'implementation/manifest.json');selected={k:Path(v) for k,v in implementation['selected'].items()}
            rel=lambda p:p.resolve().relative_to(ROOT).as_posix()
            files=[selected['placement']/n for n in ('packed.json','placement.json')]+[selected['routes']/n for n in ('routes.json','nets.json','all_nets.json','clock_tree.json')]+[selected['sta']/n for n in ('manifest.json','probe.tr','handoff_audit.json')]+[selected['wire']/'wire_delays.json',contract['cst'],contract['sdc']]
            review=dict(designs=[dict(design=a.case,selected={k:rel(v) for k,v in selected.items()},constraints=row['constraints'],metrics=observed,selected_source_sha256={rel(f):sha(f) for f in files},qualified=observed['native_constraints_satisfied'],target_mhz=row.get('target_mhz'),hardware_verified=False)])
            (out/'optimization_checkpoint.json').write_text(json.dumps(review,indent=2))
        result['status']='accepted_timing_progress' if result['accepted'] else 'rejected_global_timing'
    except Exception as error:
        result.update(status='timeout' if isinstance(error,TimeoutError) else 'failed',error=str(error));save();raise
    save();print(json.dumps(result,indent=2))


if __name__=='__main__':main()
