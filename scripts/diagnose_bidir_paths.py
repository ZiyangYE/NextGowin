"""Reconstruct exact transition-specific critical witnesses from timing DAGs."""
import argparse,json,math
from pathlib import Path
from ownpr_device import ROOT
from ownpr_image_storage import load_json,sha
from ownpr_bidir_timing import analyze,topology
from ownpr_critical_chain_move import locations
from workspace_budget import reserve


def witness(graph,metrics,endpoint,points):
    _,incoming,_=topology(graph);node=endpoint;pol=min(range(2),key=lambda i:metrics['slack'][node][i]);transition=pol;edges=[]
    while incoming[node]:
        if node in graph['seeds'] and metrics['arrival'][node][pol]==graph['seeds'][node][pol]:break
        options=[(metrics['arrival'][graph['edges'][i]['source']][a]+graph['edges'][i]['delay'][b],i,a,b)
                 for i in incoming[node] for a,b in graph['edges'][i]['transitions'] if b==pol]
        if not options:raise ValueError('Incomplete path transition')
        _,i,sp,dp=max(options);e=graph['edges'][i];src=e['source'].rsplit('/',1)[0];dst=e['destination'].rsplit('/',1)[0]
        edges.append(dict(e,selected_transition=[sp,dp],selected_delay_ns=e['delay'][dp],source_site=points.get(src),destination_site=points.get(dst)));node=e['source'];pol=sp
    edges.reverse();seed=metrics['arrival'][node][pol];arrival=metrics['arrival'][endpoint][transition];total=seed+sum(e['selected_delay_ns'] for e in edges)
    if not math.isfinite(total) or abs(total-arrival)>1e-5:raise ValueError('Reconstructed witness differs from endpoint arrival')
    return dict(endpoint=endpoint,source=node,slack_ns=metrics['endpoints'][endpoint],endpoint_transition=transition,source_arrival_ns=seed,arrival_ns=arrival,
                wire_sum_ns=sum(e['selected_delay_ns'] for e in edges if e['kind']=='wire'),nonwire_sum_ns=sum(e['selected_delay_ns'] for e in edges if e['kind']!='wire'),
                reconstruction_error_ns=abs(total-arrival),path=edges)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--model',type=Path,required=True);p.add_argument('--placement',type=Path,required=True);p.add_argument('--endpoint',action='append',default=[]);p.add_argument('--count',type=int,default=12);p.add_argument('--out',type=Path,required=True);a=p.parse_args()
    if not 1<=a.count<=64:p.error('Invalid bounded path count')
    reserve(ROOT,4_000_000);model=load_json(a.model/'model.json');graph=model['late'];metrics=analyze(graph,model['setup_requirements']);layout=load_json(a.placement/'placement.json');points=locations(layout)
    endpoints=list(dict.fromkeys(a.endpoint+[n for n,_ in sorted(metrics['endpoints'].items(),key=lambda r:(r[1],r[0]))[:a.count]]))
    rows=[witness(graph,metrics,n,points) for n in endpoints]
    record=dict(scope='Transition-specific measured-model witnesses; not a substitute for native final STA',source_sha256={str(f.resolve()):sha(f) for f in (a.model/'model.json',a.placement/'placement.json',Path(__file__))},paths=rows)
    with a.out.open('x') as stream:json.dump(record,stream,indent=2)
    print(json.dumps([{k:r[k] for k in ('source','endpoint','slack_ns','wire_sum_ns','nonwire_sum_ns')} for r in rows[:4]],indent=2))


if __name__=='__main__':main()
