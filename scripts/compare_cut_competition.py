"""Retrospective root arrival/competing-input audit after real routed cuts."""
import argparse,json,math
from pathlib import Path
from ownpr_image_storage import load_json,sha
from ownpr_device import ROOT
from workspace_budget import reserve

def checked(folder,placement):
    meta=load_json(folder/'manifest.json')
    if meta['status']!='arrival_feedback_evaluated' or meta['maximum_absolute_difference_ns']>.002:raise ValueError('Arrival model has not matched its native report')
    for name,digest in meta['source_sha256'].items():
        if sha(Path(name))!=digest:raise ValueError('Arrival evidence changed')
    for name in ('packed.json','placement.json'):
        if meta['source_sha256'].get(str((placement/name).resolve()))!=sha(placement/name):raise ValueError('Wrong placement for arrival model')
    return load_json(folder/'arrivals.json'),load_json(folder/'competitors.json')

def main():
    p=argparse.ArgumentParser()
    for name in ('before-placement','before-arrivals','after-placement','after-arrivals','plan','out'):p.add_argument('--'+name,type=Path,required=True)
    a=p.parse_args();reserve(ROOT,5_000_000);before,_=checked(a.before_arrivals,a.before_placement);after,competitors=checked(a.after_arrivals,a.after_placement)
    old=load_json(a.before_placement/'packed.json')['modules']['top']['cells'];new=load_json(a.after_placement/'packed.json')['modules']['top']['cells'];rows=[]
    for choice in load_json(a.plan)['chosen']:
        root=choice['cut']['root'];old_pin=next(p for p,d in old[root]['port_directions'].items() if d=='output');new_pin=next(p for p,d in new[root]['port_directions'].items() if d=='output')
        if old[root]['connections'][old_pin]!=new[root]['connections'][new_pin]:raise ValueError('Root output signal changed')
        b=before[root+'/'+old_pin];c=after[root+'/'+new_pin]
        if any(v is None or not math.isfinite(v) for v in b+c):raise ValueError('Uncovered cut output transition')
        winners=[]
        for tr in (0,1):
            candidates=[r for r in competitors[root+'/'+new_pin] if r['transition']==tr]
            winner=max(candidates,key=lambda r:r['arrival_ns']);winners.append(winner)
        rows.append(dict(root=root,width=choice['cut']['cut_width'],old_arrival_ns=b,new_arrival_ns=c,rise_fall_gain_ns=[b[i]-c[i] for i in (0,1)],priority_gain_proxy_ns=choice.get('priority_gain_proxy_ns'),winning_inputs=winners,data_input_wins=any(r['source'].endswith(('/I0','/I1')) for r in winners) if new[root]['type'].startswith('MUX2_LUT') else None))
    result=dict(status='retrospective_competing_input_audit',prediction_validated=False,scope='Actual post-route root arrivals including clock effects; not causal isolation or full-design acceptance',plan_sha256=sha(a.plan),before_model_sha256=sha(a.before_arrivals/'manifest.json'),after_model_sha256=sha(a.after_arrivals/'manifest.json'),roots=rows)
    with a.out.open('x') as stream:json.dump(result,stream,indent=2)
    print(json.dumps(rows,indent=2))
if __name__=='__main__':main()
