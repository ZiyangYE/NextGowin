"""Audit a routed alias candidate and expose the scope of its timing leverage.

Zero-wire projections are optimistic fixed-cell model bounds, never routes or
qualified frequencies. Full native STA remains the acceptance authority.
"""
import argparse,collections,copy,json,time
from pathlib import Path
from ownpr_image_storage import load_json,sha
from ownpr_alias_feedback import AliasFeedback
from ownpr_bidir_timing import analyze,topology
from evaluate_fixed_clock_rewire import evaluate
from ownpr_device import ROOT
from workspace_budget import reserve


def trace(graph,result,endpoint):
    _,incoming,_=topology(graph);node=endpoint;transition=min(range(2),key=lambda t:result['slack'][node][t]);path=[]
    while incoming[node]:
        candidates=[(result['arrival'][e['source']][a]+e['delay'][b],e,a,b) for i in incoming[node] for e in [graph['edges'][i]] for a,b in e['transitions'] if b==transition]
        if not candidates:raise ValueError('No predecessor for a covered transition')
        value,e,a,b=max(candidates,key=lambda x:x[0])
        if abs(value-result['arrival'][node][transition])>1e-7:raise ValueError('Critical path does not reconstruct arrival')
        path.append(dict(source=e['source'],destination=node,kind=e['kind'],delay_ns=e['delay'][b]));node=e['source'];transition=a
    return dict(endpoint=endpoint,slack_ns=result['endpoints'][endpoint],seed=node,seed_arrival_ns=result['arrival'][node][transition],
                delay_by_kind_ns={kind:sum(e['delay_ns'] for e in path if e['kind']==kind) for kind in {e['kind'] for e in path}},arcs=list(reversed(path)))


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for n in ('model','placement','routes','wire','out'):p.add_argument('--'+n,type=Path,required=True)
    a=p.parse_args();reserve(ROOT,3_000_000);start=time.time()
    report,graphs,model=evaluate(a.model,a.placement,a.routes,a.wire,True)
    mm=load_json(a.model/'manifest.json');base=next(Path(f) for f in mm['source_sha256'] if Path(f).name=='packed.json');sta=next(Path(f).parent for f in mm['source_sha256'] if Path(f).name=='handoff_audit.json')
    caps=load_json(sta/'sta_endpoint_audit.json');ids={r['node'][2] for c in caps for r in c['nodes']}
    class NativeCoordinates:
        class device:names={i:i for i in ids}
        def node(self,y,x,i):return (y+1,x+1,i)
    pm=load_json(a.placement/'manifest.json');aliases=pm['register_aliases'];reference=load_json(base)
    feedback=AliasFeedback(model,caps,NativeCoordinates(),reference,aliases)
    wire=load_json(a.wire/'wire_delays.json')
    candidate=load_json(a.placement/'packed.json');changed=set()
    for n,c in reference['modules']['top']['cells'].items():
        for pin,bs in c['connections'].items():
            ds=candidate['modules']['top']['cells'][n]['connections'][pin]
            if bs!=ds:changed.update('net_'+str(bit) for bit in bs+ds)
    measurements={n:{tuple(s['node']):s['delays_by_selector'][1][2:4]+s['delays_by_selector'][2][:2] for s in row['sinks']} for n,row in wire['nets'].items() if n in changed}
    typed=feedback.update(dict(setup=model['late'],hold=model['early']),candidate,measurements)
    metrics=feedback.metrics(typed)
    for name in ('setup','hold'):
        if abs(metrics[name]['wns']-report[name]['wns'])>1e-6 or abs(metrics[name]['tns']-report[name]['tns'])>1e-4:raise ValueError('Typed and file feedback differ')
    late=graphs['setup'];current=analyze(late,model['setup_requirements']);members=set(aliases)|set(aliases.values());bounds={};families=collections.defaultdict(set)
    for replica,parent in aliases.items():families[parent].update((replica,parent))
    for name,predicate in [('zero_existing_alias_output_wires',lambda e:e['source'].rsplit('/',1)[0] in members),('zero_all_data_wires',lambda e:True)]:
        seeds=copy.deepcopy(late['seeds'])
        if name=='zero_existing_alias_output_wires':
            # Rewiring can choose a replica with a different clock-to-Q/clock
            # arrival. An optimistic bound must also grant the earliest seed
            # within that family, independently for each transition.
            for family in families.values():
                labels=[n+'/Q' for n in family]
                fastest=[min(seeds[n][t] for n in labels) for t in (0,1)]
                for n in labels:seeds[n]=list(fastest)
        projected=dict(late,seeds=seeds,edges=[dict(e,delay=[0.,0.]) if e['kind']=='wire' and predicate(e) else e for e in late['edges']]);r=analyze(projected,model['setup_requirements'])
        bounds[name]={k:r[k] for k in ('wns','tns','violations')}
    top=sorted(current['endpoints'],key=current['endpoints'].get)[:20]
    result=dict(status='typed_alias_feedback_revalidated',timing=report,optimistic_projections=bounds,
                projection_scope='Unrealizable zero-wire fixed-cell model projections at original 110 MHz requirements. Alias projection also grants componentwise fastest source arrivals within each existing family. No placement, routing, or native qualification.',
                critical_paths=[trace(late,current,n) for n in top],sources={str(f):sha(f) for f in (a.placement/'packed.json',a.routes/'routes.json',a.wire/'wire_delays.json',Path(__file__),ROOT/'scripts/ownpr_alias_feedback.py')},wall_seconds=time.time()-start)
    with a.out.open('x') as stream:json.dump(result,stream,indent=2)
    print(json.dumps(dict(status=result['status'],timing={n:report[n] for n in ('setup','hold')},optimistic_projections=bounds),indent=2))


if __name__=='__main__':main()
