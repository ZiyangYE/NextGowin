"""Measured tree refinement and hold closure within an attempt's original deadline."""
import argparse,json,math,re,shutil,subprocess,sys,time
from datetime import datetime,timezone
from pathlib import Path
from ownpr_device import ROOT
from ownpr_image_storage import load_json,sha,read_text
from workspace_budget import reserve
from run_ownpr_pin_closure import stop_owned_process_tree


def main():
    p=argparse.ArgumentParser();p.add_argument('--attempt',type=Path,required=True);p.add_argument('--base-report',type=Path,required=True);p.add_argument('--out',type=Path,required=True);a=p.parse_args()
    original=load_json(a.attempt/'manifest.json');started=datetime.fromisoformat(original['started_utc']);budget=original['budget_seconds']
    if original['status']!='rejected' or not 1<=budget<=3600 or sha(a.base_report)!=original['source_report_sha256']:raise ValueError('Expected a completed rejected attempt with unchanged baseline')
    def remain():return budget-(datetime.now(timezone.utc)-started).total_seconds()
    if remain()<1000:raise TimeoutError('Insufficient original deadline for refinement and final qualification')
    reserve(ROOT,2_500_000_000);out=a.out.resolve();out.mkdir(exist_ok=False);shutil.copyfile(__file__,out/'runner_source.py.txt')
    row=load_json(a.attempt/'replay_checkpoint.json')['designs'][0]
    base=next(r for r in load_json(a.base_report)['designs'] if r.get('design')==row['design'])
    for review in (base,row):
        for name,h in review['selected_source_sha256'].items():
            if sha(ROOT/name)!=h:raise ValueError('Reviewed source changed')
    state={k:ROOT/v for k,v in row['selected'].items()};contract={k:ROOT/v for k,v in row['constraints'].items()};metrics=row['metrics']
    result=dict(status='running',original_attempt=str(a.attempt),original_manifest_sha256=sha(a.attempt/'manifest.json'),original_started_utc=started.isoformat(),budget_seconds=budget,stages=[],initial_metrics=metrics,accepted=False,goal_complete=False,vendor_pnr_executed=False)
    def save():
        result.update(total_elapsed_upper_bound_seconds=budget-remain(),selected={k:str(v) for k,v in state.items()},metrics=metrics)
        (out/'manifest.json').write_text(json.dumps(result,indent=2))
    def run(label,script,args):
        if remain()<=0:raise TimeoutError('Original complete attempt deadline expired')
        cmd=[sys.executable,str(ROOT/script),*map(str,args)];entry=dict(stage=label,command=cmd,status='running');result['stages'].append(entry);save();stamp=time.monotonic()
        with (out/(label+'.log')).open('w') as log:
            proc=subprocess.Popen(cmd,cwd=ROOT,stdout=log,stderr=subprocess.STDOUT,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            try:code=proc.wait(timeout=remain())
            except subprocess.TimeoutExpired:stop_owned_process_tree(proc);raise TimeoutError('Original complete attempt deadline expired')
        entry.update(status='complete' if code==0 else 'failed',returncode=code,wall_seconds=time.monotonic()-stamp);save()
        if code:raise RuntimeError('Refinement stage failed: '+label)
    def review_state():
        rel=lambda p:Path(p).resolve().relative_to(ROOT).as_posix()
        files=[state['placement']/'packed.json',state['placement']/'placement.json',*[state['routes']/n for n in ('routes.json','nets.json','all_nets.json','clock_tree.json')],*[state['sta']/n for n in ('manifest.json','probe.tr','handoff_audit.json')],state['wire']/'wire_delays.json']
        return dict(designs=[dict(design=row['design'],selected={k:rel(v) for k,v in state.items()},metrics=metrics,constraints={k:rel(v) for k,v in contract.items()},selected_source_sha256={rel(f):sha(f) for f in files})])
    save()
    try:
        run('capture','experiments/native_pin_feedback_probe.py',['--source',state['sta'],'--placement',state['placement'],'--out',out/'pin_feedback'])
        history_compatible=sha(ROOT/base['selected']['placement']/'packed.json')==sha(state['placement']/'packed.json')
        history_args=['--source-history',a.base_report] if history_compatible else []
        result['historical_reattachment']=dict(enabled=history_compatible,reason='identical netlist' if history_compatible else 'logical transform changed netlist; ordinary tree reconstruction only')
        run('trees','scripts/ownpr_critical_tree_rebuild.py',['--placement',state['placement'],'--routes',state['routes'],'--sta',state['sta'],'--pin-feedback',out/'pin_feedback',
            '--count',3,'--seconds',min(240,int(remain())-750),'--search-mode','both','--include-single-sink','--out',out/'route']+history_args)
        rm=load_json(out/'route/manifest.json')
        if rm['critical_tree_rebuild']:
            run('tree_export','scripts/export_ownpr.py',['--placement',state['placement'],'--routes',out/'route','--clock-capture',state['clock_capture'],'--cst',contract['cst'],'--sdc',contract['sdc'],
                '--io-reference',contract['io_reference'],'--seconds',min(450,int(remain())-650),'--out',out/'tree_export'])
            cm=load_json(out/'tree_export/manifest.json')['metrics'];result['tree_metrics']=cm
            keep=cm['other_checks_passed'] and cm['hold_violations']<=metrics['hold_violations'] and cm['hold_wns_ns']>=metrics['hold_wns_ns']-.001 and cm['setup_wns_ns']>metrics['setup_wns_ns']+.001
            result['tree_refinement_retained']=keep
            if keep:state=dict(state,routes=out/'route',wire=out/'tree_export/wire',sta=out/'tree_export/native');metrics=cm
        else:result['tree_refinement_retained']=False
        save()
        if metrics['hold_violations']:
            (out/'hold_source.json').write_text(json.dumps(dict(selected={k:str(v) for k,v in state.items()}),indent=2))
            if remain()<650:raise TimeoutError('No original budget for hold repair and qualification')
            run('hold','scripts/run_ownpr_hold_closure.py',['--state',out/'hold_source.json','--cst',contract['cst'],'--sdc',contract['sdc'],'--io-reference',contract['io_reference'],
                '--rounds',1,'--count',3,'--branch-limit',1,'--seconds',min(650,int(remain())-280),'--out',out/'hold'])
            hm=load_json(out/'hold/manifest.json');candidate=hm['exploration_state']
            if hm['rounds'] and hm['rounds'][-1].get('decision')=='accepted_hard_constraints_repaired':candidate=hm['selected']
            state={k:Path(candidate[k]) for k in ('placement','routes','wire','sta','clock_capture')};metrics=load_json(state['sta'].parent/'manifest.json')['metrics'];save()
        review=review_state();(out/'replay_checkpoint.json').write_text(json.dumps(review,indent=2))
        if metrics['other_checks_passed'] and metrics['hold_violations']==0:
            period=float(re.search(r'create_clock\s+-name\s+clk\s+-period\s+([\d.]+)',read_text(contract['sdc']))[1])
            # Full I/O WNS bounds the starting frequency, in addition to Fmax.
            target=math.floor(min(metrics['reported_fmax_mhz']-.05,1000/(period-metrics['setup_wns_ns']+.02))*10)/10
            if remain()<250:raise TimeoutError('No original budget for complete frequency qualification')
            run('qualification','scripts/replay_coopt_checkpoint.py',['--case',row['design'],'--report',out/'replay_checkpoint.json','--target-mhz',target,'--seconds',int(remain())-1,'--out',out/'qualification'])
            qm=load_json(out/'qualification/manifest.json')['metrics'];result.update(qualified_mhz=target,qualified_metrics=qm)
            qualified=qm['native_constraints_satisfied'] and qm['other_checks_passed'] and qm['setup_violations']==qm['hold_violations']==0
            if qualified:
                state=dict(state,sta=out/'qualification/export/native');metrics=qm;contract=dict(contract,sdc=out/'qualification/clock.sdc')
                (out/'qualified_replay_checkpoint.json').write_text(json.dumps(review_state(),indent=2))
                oldperiod=float(re.search(r'create_clock\s+-name\s+clk\s+-period\s+([\d.]+)',read_text(ROOT/base['constraints']['sdc']))[1])
                result.update(accepted=target>1000/oldperiod+.01,frequency_qualified=True)
        result['status']='accepted' if result['accepted'] else 'rejected';save()
    except Exception as e:
        result.update(status='timeout' if isinstance(e,(TimeoutError,subprocess.TimeoutExpired)) else 'failed',error=str(e));save();raise
    print(json.dumps({k:result.get(k) for k in ('status','total_elapsed_upper_bound_seconds','qualified_mhz','metrics')},indent=2))


if __name__=='__main__':main()
