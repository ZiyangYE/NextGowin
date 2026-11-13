"""Bounded same-CFU LUT-slot search driven by full timing reports.

Uses no design-specific names. Each move is independently routed/exported;
only full-contract timing improvements update the selected implementation.
"""
from ownpr_image_storage import read_text as read_artifact_text
import argparse,json,subprocess,sys,time
from pathlib import Path
from ownpr_device import ROOT
from ownpr_lut_slot import candidates
from ownpr_place import sha
from workspace_budget import reserve
from run_ownpr_pin_closure import metric,stop_owned_process_tree


def read(p):return json.loads(Path(p).read_text())


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for n in ('state','cst','sdc','io-reference','out'):p.add_argument('--'+n,type=Path,required=True)
    p.add_argument('--attempts',type=int,default=4);p.add_argument('--seconds',type=int,default=600);a=p.parse_args()
    if not 1<=a.attempts<=20 or not 1<=a.seconds<=3600:p.error('Require 1..20 attempts and 1..3600 seconds')
    start=time.monotonic();deadline=start+a.seconds;storage=reserve(ROOT,a.attempts*500_000_000)
    state=read(a.state)['selected'];out=a.out.resolve();out.mkdir(exist_ok=False)
    report=dict(status='running',initial=state,selected=state,initial_metrics=metric(Path(state['sta'])),attempts=[],budget_seconds=a.seconds,
        storage_reservation=storage,source_state_sha256=sha(a.state),runner_sha256=sha(__file__),candidate_generator_sha256=sha(ROOT/'scripts/ownpr_lut_slot.py'),
        vendor_placement_executed=False,vendor_routing_executed=False,timing_signoff=False,full_bitstream_verified=False)
    report['metrics']=report['initial_metrics'];tried=set()
    def save():report['wall_seconds']=time.monotonic()-start;(out/'manifest.json').write_text(json.dumps(report,indent=2))
    save()
    for index in range(a.attempts):
        remaining=int(deadline-time.monotonic())
        if remaining<110:report['stop_reason']='insufficient_remaining_complete_attempt_budget';break
        placement=Path(state['placement']);sta=Path(state['sta']);meta=read(sta/'manifest.json')
        if meta['packed_sha256']!=sha(placement/'packed.json') or meta['placement_sha256']!=sha(placement/'placement.json'):
            raise ValueError('Slot feedback does not match selected placement')
        key=sha(placement/'placement.json')
        options=candidates(read(placement/'packed.json'),read(placement/'placement.json'),read_artifact_text(sta/'probe.tr'),read(sta/'name_map.json'))
        options=[c for c in options if (key,c['instance'],c['slot']) not in tried]
        if not options:report['stop_reason']='enumerated_neighborhood_exhausted';break
        candidate=options[0];tried.add((key,candidate['instance'],candidate['slot']));folder=out/f'attempt_{index:03d}'
        command=[sys.executable,str(ROOT/'scripts/run_ownpr_cluster_swap.py')]
        for k in ('placement','routes','wire','sta','clock_capture'):
            command+=['--'+{'wire':'wire-timing','clock_capture':'clock-capture'}.get(k,k),state[k]]
        command+=['--cst',str(a.cst.resolve()),'--sdc',str(a.sdc.resolve()),'--io-reference',str(a.io_reference.resolve()),
            '--lut-slot',candidate['instance'],str(candidate['slot']),'--seconds',str(min(remaining-2,600)),'--out',str(folder)]
        row=dict(proposal=candidate,source=dict(state),source_placement_sha256=key,command=command,status='running');report['attempts'].append(row);save()
        with (out/f'attempt_{index:03d}.log').open('w') as log:
            proc=subprocess.Popen(command,cwd=ROOT,stdout=log,stderr=subprocess.STDOUT,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            try:code=proc.wait(timeout=max(1,deadline-time.monotonic()))
            except subprocess.TimeoutExpired:
                stop_owned_process_tree(proc);row['status']='timeout';report['status']='timeout';save();return
        row['returncode']=code
        if code:
            row['status']='failed';row['error']=read(folder/'manifest.json').get('error') if (folder/'manifest.json').exists() else 'no manifest'
        else:
            result=read(folder/'manifest.json');state=result['selected'];row.update(status=result['evaluations'][0]['decision'],metrics=result['evaluations'][0]['metrics'])
            report.update(selected=state,metrics=result['metrics'])
        save()
    report['status']='bounded_slot_search_complete';save();print(json.dumps({k:report[k] for k in ('status','wall_seconds','metrics')},indent=2))


if __name__=='__main__':main()
