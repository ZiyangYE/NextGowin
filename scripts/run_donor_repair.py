"""Run seed admission, bounded rerouting and full export on the original clock."""
import argparse,json,subprocess,sys,time
from datetime import datetime,timezone
from pathlib import Path
from ownpr_device import ROOT
from ownpr_image_storage import load_json
from run_ownpr_pin_closure import stop_owned_process_tree
from workspace_budget import reserve

def main():
    p=argparse.ArgumentParser();p.add_argument('--attempt',type=Path,required=True);p.add_argument('--out',type=Path,required=True);p.add_argument('--state-report',type=Path);a=p.parse_args();attempt=a.attempt.resolve();out=a.out.resolve()
    original=load_json(attempt/'manifest.json');started=datetime.fromisoformat(original['started_utc']);budget=original['budget_seconds']
    remain=lambda:budget-(datetime.now(timezone.utc)-started).total_seconds()
    if remain()<600:raise TimeoutError('Insufficient original attempt budget')
    from ownpr_image_storage import sha
    reserve(ROOT,1_200_000_000);base=load_json(attempt/'replay_checkpoint.json')['designs'][0];row=load_json(a.state_report)['designs'][0] if a.state_report else base
    if row['constraints']!=base['constraints']:raise ValueError('Repair chain changed constraints')
    if sha(ROOT/row['selected']['placement']/'packed.json')!=sha(ROOT/base['selected']['placement']/'packed.json') or sha(ROOT/row['selected']['placement']/'placement.json')!=sha(ROOT/base['selected']['placement']/'placement.json'):raise ValueError('Repair chain changed logic or placement')
    for path,digest in row['selected_source_sha256'].items():
        if sha(ROOT/path)!=digest:raise ValueError('Repair state evidence changed')
    state={k:ROOT/v for k,v in row['selected'].items()};contract={k:ROOT/v for k,v in row['constraints'].items()}
    native=load_json(state['sta']/'manifest.json');route_meta=load_json(state['routes']/'manifest.json')
    if route_meta['vendor_pnr_executed'] or native['vendor_placement_executed'] or native['vendor_routing_executed']:raise ValueError('Repair source is not own P&R')
    if sha(contract['cst'])!=native['cst_sha256'] or sha(contract['sdc'])!=native['source_sdc_sha256']:raise ValueError('Repair source constraint bytes changed')
    implementation=load_json(attempt/'implementation/manifest.json');route=next(s['command'] for s in implementation['stages'] if s['stage']=='route')
    command=[];i=0
    while i<len(route):
        if route[i] in ('--out','--timeout','--reuse-routes'):i+=2;continue
        if route[i]=='--reuse-branches':i+=1;continue
        command.append(route[i]);i+=1
    command+=['--out',str(out/'route'),'--timeout',str(min(600,int(remain())-400)),'--resume-checkpoint',str(out/'seed')]
    record=dict(status='running',original_attempt=str(attempt),original_started_utc=started.isoformat(),budget_seconds=budget,stages=[],accepted=False)
    def save():record['total_attempt_elapsed_seconds']=budget-remain();(out/'manifest.json').write_text(json.dumps(record,indent=2))
    def run(label,args):
        if remain()<=0:raise TimeoutError('Original deadline expired')
        record['stages'].append(dict(stage=label,command=args));save()
        with (out/(label+'.log')).open('w') as log:
            proc=subprocess.Popen(args,cwd=ROOT,stdout=log,stderr=subprocess.STDOUT,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            try:code=proc.wait(timeout=remain())
            except subprocess.TimeoutExpired:stop_owned_process_tree(proc);raise
        if code:raise RuntimeError(label+' failed: '+str(code))
    try:
        run('route',command);rm=load_json(out/'route/manifest.json')
        if rm['checkpoint_resume']['audit']['rejected']:raise ValueError('Seed admission released unexpected nets')
        seed=load_json(out/'seed/manifest.json');before=load_json(state['routes']/'routes.json')['nets'];after=load_json(out/'route/routes.json')['nets']
        if set(before)!=set(after):raise ValueError('Repair changed net inventory')
        changed={n for n in before if set(map(tuple,before[n]['pips']))!=set(map(tuple,after[n]['pips']))}
        allowed={seed['donor_net'],*seed['released_blockers']}
        if not changed<=allowed:raise ValueError('Repair escaped bounded conflict closure')
        record['closure_audit']=dict(changed_nets=sorted(changed),allowed_nets=sorted(allowed),other_routes_preserved=True)
        run('export',[sys.executable,str(ROOT/'scripts/export_ownpr.py'),'--placement',str(state['placement']),'--routes',str(out/'route'),'--clock-capture',str(state['clock_capture']),'--cst',str(contract['cst']),'--sdc',str(contract['sdc']),'--io-reference',str(contract['io_reference']),'--seconds',str(int(remain())-1),'--out',str(out/'export')])
        record.update(status='evaluated',metrics=load_json(out/'export/manifest.json')['metrics'])
    except Exception as error:record.update(status='failed',error=str(error));save();raise
    save();print(json.dumps(record['metrics']))
if __name__=='__main__':main()
