"""Bounded own LUT-pin ECO loop with CEC, configuration and full native STA.

The vendor is used only for queries, final timing and serialization. A local
arc improvement is committed to the next iteration only after global checks.
"""
from ownpr_image_storage import read_text as read_artifact_text
import argparse,json,re,shutil,subprocess,sys,time
from pathlib import Path
from ownpr_device import ROOT
from ownpr_place import sha
from ownpr_timing_numbers import negative_slack
from workspace_budget import reserve


def stop_owned_process_tree(process):
    """Stop only this stage and its descendants, including native workers."""
    import psutil
    if process.poll() is not None:return
    root=psutil.Process(process.pid);owned=[root]
    # Freeze the owner before collecting workers so the wrapper cannot spawn
    # a new native subprocess while its timeout is being handled.
    try:root.suspend()
    except psutil.NoSuchProcess:return
    try:
        for child in root.children(recursive=True):
            try:child.suspend();owned.append(child)
            except psutil.NoSuchProcess:pass
        for child in root.children(recursive=True):
            if child not in owned:owned.append(child)
        for child in reversed(owned):
            try:child.kill()
            except psutil.NoSuchProcess:pass
        psutil.wait_procs(owned,timeout=10);process.wait(timeout=10)
    finally:
        for child in owned:
            try:
                if child.is_running():child.resume()
            except psutil.NoSuchProcess:pass


def metric(folder):
    folder=Path(folder);meta=json.loads((folder/'manifest.json').read_text());audit=json.loads((folder/'handoff_audit.json').read_text())
    if meta['status']!='imported' or not meta['sta_completed'] or meta['handoff_audit_sha256']!=sha(folder/'handoff_audit.json'):raise ValueError('Pin closure requires a verified native handoff')
    if meta['vendor_placement_executed'] or meta['vendor_routing_executed']:raise ValueError('Vendor P&R result cannot enter own closure loop')
    t=audit['timing'];coverage=t['path_coverage'];text=read_artifact_text(folder/'probe.tr')
    section=re.search(r'^3\.1\.1 Setup Paths Table\n(.*?)(?=\n3\.)',text,re.S|re.M)
    if not section:raise ValueError('Missing final setup paths')
    slacks=[float(s) for s in re.findall(r'^\s*\d+\s+([-\d.]+)\s+own_i_\d+/\S+\s+own_i_\d+/\S+\s+',section[1],re.M)]
    if len(slacks)!=coverage['setup']['paths']:raise ValueError('Incomplete closure setup report')
    return dict(setup_wns_ns=coverage['setup']['worst_slack_ns'],hold_wns_ns=coverage['hold']['worst_slack_ns'],
        setup_violations=t['setup_violated_endpoints'],hold_violations=t['hold_violated_endpoints'],
        total_negative_setup_slack_ns=round(sum(min(0,s) for s in slacks),6),reported_fmax_mhz=t['reported_fmax_mhz'],
        other_checks_passed=t.get('async_constraints_passed',True) and t.get('minimum_pulse_width_passed',not negative_slack(t['minimum_pulse_width_slack_ns'])),
        native_constraints_satisfied=audit['native_sta_constraints_satisfied'],handoff_sha256=sha(folder/'handoff_audit.json'))


def improves(before,after):
    if after['hold_violations'] or negative_slack(after['hold_wns_ns']) or not after['other_checks_passed']:return False
    if before['hold_violations'] or negative_slack(before['hold_wns_ns']) or not before['other_checks_passed']:return True
    return (after['setup_wns_ns'],-after['setup_violations'],after['total_negative_setup_slack_ns'])>(before['setup_wns_ns'],-before['setup_violations'],before['total_negative_setup_slack_ns'])


def main():
    p=argparse.ArgumentParser();p.add_argument('--placement',type=Path,required=True);p.add_argument('--routes',type=Path,required=True);p.add_argument('--sta',type=Path,required=True)
    p.add_argument('--clock-capture',type=Path,required=True);p.add_argument('--cst',type=Path,required=True);p.add_argument('--sdc',type=Path,required=True);p.add_argument('--io-reference',type=Path,required=True)
    p.add_argument('--wire-timing',type=Path,help='Existing source wire measurements, retained in the resumable state')
    p.add_argument('--out',type=Path,required=True);p.add_argument('--rounds',type=int,default=4);p.add_argument('--seconds',type=int,default=1800);p.add_argument('--candidates-per-state',type=int,default=4);p.add_argument('--first-rank',type=int,default=0);a=p.parse_args()
    if not 1<=a.rounds<=20 or not 1<=a.seconds<=3600:p.error('Bounded closure requires 1..20 rounds and at most one hour')
    if not 1<=a.candidates_per_state<=10 or not 0<=a.first_rank<a.candidates_per_state:p.error('Invalid candidate retry budget')
    candidate_rank=a.first_rank
    start=time.monotonic();deadline=start+a.seconds;storage=reserve(ROOT,(a.rounds+1)*500_000_000)
    out=a.out.resolve();out.mkdir(exist_ok=False)
    shutil.copyfile(__file__,out/'runner_source.py.txt')
    initial_meta=json.loads((a.sta/'manifest.json').read_text())
    if initial_meta['cst_sha256']!=sha(a.cst) or initial_meta['source_sdc_sha256']!=sha(a.sdc):raise ValueError('Closure must preserve the initial I/O and timing constraints')
    selected=dict(placement=str(a.placement.resolve()),routes=str(a.routes.resolve()),sta=str(a.sta.resolve()),clock_capture=str(a.clock_capture.resolve()))
    if a.wire_timing:selected['wire']=str(a.wire_timing.resolve())
    best=metric(a.sta);history=[]
    result=dict(status='running',initial=dict(selected),initial_metrics=best,selected=dict(selected),metrics=best,rounds=history,
        budget_seconds=a.seconds,maximum_attempts=a.rounds,candidates_per_state=a.candidates_per_state,first_rank=a.first_rank,runner_sha256=sha(out/'runner_source.py.txt'),
        vendor_pnr_executed=False,open_chip_database_used=False,full_bitstream_verified=False)
    result['storage_reservation']=storage
    def save():
        result['wall_seconds']=time.monotonic()-start;(out/'manifest.json').write_text(json.dumps(result,indent=2))
    def run(round_dir,stage,script,args,timeout=240):
        reserve(ROOT,500_000_000)
        remaining=deadline-time.monotonic()
        if remaining<=1:raise TimeoutError('One-hour/selected closure budget exhausted')
        command=[sys.executable,str(ROOT/script),*map(str,args)];stamp=time.monotonic()
        timed_out=False
        with (round_dir/(stage+'.log')).open('w') as log:
            process=subprocess.Popen(command,cwd=ROOT,stdout=log,stderr=subprocess.STDOUT,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            try:code=process.wait(timeout=min(timeout,remaining))
            except subprocess.TimeoutExpired:
                timed_out=True;stop_owned_process_tree(process);code=process.returncode
        history[-1]['stages'].append(dict(stage=stage,returncode=code,timeout=timed_out,seconds=time.monotonic()-stamp,command=command));save()
        print('PIN_CLOSURE_STAGE',round_dir.name,stage,code,flush=True)
        if timed_out:raise TimeoutError(stage+' exceeded its stage/closure budget; owned process tree stopped')
        if code:raise RuntimeError(stage+' failed; retained '+str(round_dir/(stage+'.log')))
    save()
    for index in range(1,a.rounds+1):
        rd=out/f'round_{index:03d}';rd.mkdir();eco=rd/'eco';wire=rd/'wire';config=rd/'partial_configuration.json';export=rd/'export';entry=dict(round=index,status='running',candidate_rank=candidate_rank,before=best,stages=[]);history.append(entry);save()
        try:
            run(rd,'pin_eco','scripts/ownpr_lut_pin_eco.py',['--placement',selected['placement'],'--routes',selected['routes'],'--sta',selected['sta'],'--use-unused-inputs','--candidate-rank',candidate_rank,'--out',eco])
            run(rd,'wire','scripts/measure_ownpr_wire_delays.py',['--routes',eco,'--out',wire])
            run(rd,'joint','scripts/audit_ownpr_joint_routes.py',['--placement',eco/'placement','--routes',eco,'--wire',wire])
            run(rd,'configuration','scripts/merge_ownpr_configuration.py',['--placement',eco/'placement','--routes',eco,'--clock-capture',a.clock_capture.resolve(),'--out',config])
            run(rd,'final_sta_encoder','experiments/native_import_ownpr.py',['--placement',eco/'placement','--routes',eco,'--wire-timing',wire,'--partial',config,
                '--cst',a.cst.resolve(),'--sdc',a.sdc.resolve(),'--clock-tree',eco/'clock_tree.json','--io-reference',a.io_reference.resolve(),'--export','--sta','--out',export])
            after=metric(export);entry['after']=after;entry['pin_permutation']=json.loads((eco/'manifest.json').read_text())['pin_permutation']
            if not improves(best,after):
                entry['status']='rejected_global_timing';candidate_rank+=1;save()
                if candidate_rank<min(a.candidates_per_state,entry['pin_permutation']['legal_candidate_count']):continue
                result['status']='stopped_without_further_global_improvement';save();break
            entry['status']='accepted';candidate_rank=0;best=after;selected=dict(placement=str(eco/'placement'),routes=str(eco),wire=str(wire),sta=str(export),clock_capture=str(a.clock_capture.resolve()));result.update(selected=dict(selected),metrics=best);save()
            print('PIN_CLOSURE_ACCEPT',index,json.dumps(best),flush=True)
            if best['native_constraints_satisfied']:result['status']='supported_native_constraints_satisfied';save();break
        except (RuntimeError,TimeoutError,subprocess.TimeoutExpired) as exc:
            entry.update(status='failed_or_bounded',error=str(exc));result['status']='stopped_with_retained_best';save();break
    else:result['status']='bounded_rounds_complete';save()
    print(json.dumps({k:v for k,v in result.items() if k!='rounds'},indent=2))


if __name__=='__main__':main()
