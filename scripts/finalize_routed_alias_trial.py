"""Qualify a measured subtree trial within its original, shared deadline."""
import argparse,copy,json,subprocess,sys,time
from pathlib import Path
from ownpr_device import ROOT
from ownpr_image_storage import load_json,sha,compact_json,compact,compact_text
from workspace_budget import reserve
from run_ownpr_pin_closure import stop_owned_process_tree


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for n in ('attempt','candidate','budget'):p.add_argument('--'+n,type=Path,required=True)
    a=p.parse_args();out=a.attempt.resolve();candidate=a.candidate.resolve();budget=load_json(a.budget)
    def remaining():return budget['deadline_epoch']-time.time()
    if remaining()<450:raise TimeoutError('Insufficient original attempt time for qualification')
    reserve(ROOT,3_000_000);em=load_json(out/'export/manifest.json');cm=load_json(candidate/'manifest.json')
    if em['status']!='verified_handoff_complete' or not em['sta_completed'] or not em['bitstream_generated']:raise ValueError('No final native handoff')
    receipts=[]
    for f in (out/'export/partial_configuration.json',out/'export/native/partial_configuration.json',out/'export/native/probe.fs',out/'export/native/probe.tr'):
        if f.is_file():
            reserve(ROOT,f.stat().st_size+1_000_000);receipts.append((compact_json if f.suffix=='.json' else compact if f.suffix=='.fs' else compact_text)(f))
    with (out/'storage_compaction.json').open('x') as stream:json.dump(receipts,stream,indent=2)
    base=load_json(ROOT/'results/vexriscv_frontier_partition_ablation_001/qualified_replay_checkpoint.json')['designs'][0]
    row=copy.deepcopy(base)
    state=dict(placement=candidate/'placement',routes=candidate,wire=out/'export/wire',sta=out/'export/native',clock_capture=ROOT/'results/vexriscv_frontier_partition_ablation_001/clock_encoding')
    contract=dict(cst=ROOT/'results/vexriscv_evaluation_pins_001/fixed.cst',sdc=ROOT/'results/vexriscv_110_reference_001/clock.sdc',io_reference=ROOT/'results/native_vexriscv_packed_io_reference_001')
    rel=lambda f:Path(f).resolve().relative_to(ROOT).as_posix()
    def review():
        files=[state['placement']/'packed.json',state['placement']/'placement.json',*[state['routes']/n for n in ('routes.json','nets.json','all_nets.json','clock_tree.json','manifest.json')],*[state['sta']/n for n in ('manifest.json','probe.tr','handoff_audit.json')],state['wire']/'wire_delays.json',contract['cst'],contract['sdc'],out/'architectural_analysis.json']
        row.update(selected={k:rel(v) for k,v in state.items()},constraints={k:rel(v) for k,v in contract.items()},metrics=metrics,selected_source_sha256={rel(f):sha(f) for f in files})
        return dict(designs=[row])
    metrics=em['metrics'];row.update(qualified=False,qualified_mhz=None,target_mhz=110,target_achieved=False,goal_complete=False,hardware_verified=False)
    row['functional_certificate_chain'][rel(candidate/'placement/manifest.json')]=sha(candidate/'placement/manifest.json')
    with (out/'replay_checkpoint.json').open('x') as stream:json.dump(review(),stream,indent=2)
    reserve(ROOT,1_050_000_000);seconds=min(600,int(remaining())-100)
    command=[sys.executable,str(ROOT/'scripts/replay_coopt_checkpoint.py'),'--case','vexriscv','--report',str(out/'replay_checkpoint.json'),'--target-mhz','88.1','--seconds',str(seconds),'--out',str(out/'qualification')]
    with (out/'qualification.log').open('x') as log:
        proc=subprocess.Popen(command,cwd=ROOT,stdout=log,stderr=subprocess.STDOUT,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        try:code=proc.wait(timeout=min(seconds+5,remaining()-50))
        except subprocess.TimeoutExpired:stop_owned_process_tree(proc);raise TimeoutError('Original attempt qualification deadline')
    if code:raise ValueError('Frequency qualification failed; see qualification.log')
    qm=load_json(out/'qualification/manifest.json')['metrics']
    accepted=qm['native_constraints_satisfied'] and qm['other_checks_passed'] and qm['setup_violations']==qm['hold_violations']==0
    summary=dict(status='qualified_routed_subtree_transfer' if accepted else 'rejected',goal_complete=False,target_mhz=110,
                 previous_qualified_mhz=87.3,qualified_mhz=88.1 if accepted else None,current110_metrics=metrics,qualification_metrics=qm,
                 lut_delta=0,register_delta=0,fixed_pins_unchanged=True,layout_byte_identical=cm['layout_byte_identical'],interface_cycles_changed=False,
                 trials=[dict(folder=f'results/vexriscv_routed_branch_search_{i:03d}',metrics=load_json(ROOT/f'results/vexriscv_routed_branch_search_{i:03d}/manifest.json')['current_metrics']) for i in (1,2)],
                 candidate_selected_by='Equal setup WNS; search 002 has lower total negative setup slack',
                 original_budget_seconds=budget['deadline_epoch']-budget['start_epoch'],total_attempt_elapsed_seconds=time.time()-budget['start_epoch'],
                 within_original_deadline=time.time()<budget['deadline_epoch'],vendor_pnr_executed=False,open_chip_database_used=False,device_programmed=False,hardware_verified=False,
                 structural_algorithm_generalization='Not evaluated on guard designs in this attempt; previous frozen-backend replay is not evidence of structural generalization',
                 gemini_report='carbon-advice-20261111-routed-branch-08.md',architecture_analysis=rel(out/'architectural_analysis.json'))
    if accepted:
        state['sta']=out/'qualification/export/native';contract['sdc']=out/'qualification/clock.sdc';metrics=qm
        row.update(qualified=True,qualified_mhz=88.1,target_mhz=88.1,total_attempt_elapsed_seconds=summary['total_attempt_elapsed_seconds'],original_budget_seconds=3500,new_algorithm_generalization_proven=False)
        with (out/'qualified_replay_checkpoint.json').open('x') as stream:json.dump(review(),stream,indent=2)
    summary['workspace_bytes']=reserve(ROOT,0)['current_bytes']
    with (out/'engineering_summary.json').open('x') as stream:json.dump(summary,stream,indent=2)
    print(json.dumps(summary,indent=2))


if __name__=='__main__':main()
