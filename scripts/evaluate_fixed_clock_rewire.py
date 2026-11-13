"""Evaluate complete measured trees for a fixed-layout state-alias rewrite.

All data wire arcs are refreshed, including unchanged receivers on changed
trees. Clock, state, cells and constraints must remain fixed. This does not
replace final native STA or admit relocation/new primitive kinds.
"""
import argparse,copy,json,time
from pathlib import Path
from ownpr_image_storage import load_json,sha
from ownpr_bidir_timing import analyze
from ownpr_timing_pins import pins
from workspace_budget import reserve
from ownpr_device import ROOT


def evaluate(model_dir,placement,routes,wire,return_graphs=False):
    meta=load_json(model_dir/'manifest.json');model=load_json(model_dir/'model.json')
    if meta['status']!='validated_setup_hold_feedback' or sha(model_dir/'model.json')!=meta['model_sha256']:raise ValueError('Unvalidated timing feedback')
    for path,h in meta['source_sha256'].items():
        if sha(Path(path))!=h:raise ValueError('Timing feedback source changed')
    packed=[Path(p) for p in meta['source_sha256'] if Path(p).name=='packed.json'];audit=[Path(p) for p in meta['source_sha256'] if Path(p).name=='handoff_audit.json']
    if len(packed)!=1 or len(audit)!=1:raise ValueError('Ambiguous model binding')
    base=packed[0].parent;sta=audit[0].parent;old=load_json(packed[0]);new=load_json(placement/'packed.json');pm=load_json(placement/'manifest.json')
    if sha(placement/'placement.json')!=sha(base/'placement.json'):raise ValueError('State relocation requires new clock/timing admission')
    if sha(routes/'clock_tree.json')!=sha(sta/'clock_tree.json'):raise ValueError('Clock topology changed')
    if pm['source_netlist_sha256']!=sha(packed[0]) or pm['proof']['status']!='joint_existing_alias_assignment_proved':raise ValueError('Missing joint alias proof')
    a=old['modules']['top'];b=new['modules']['top'];aliases=pm['register_aliases'];canonical={a['cells'][n]['connections']['Q'][0]:a['cells'][p]['connections']['Q'][0] for n,p in aliases.items()}
    if {k:v for k,v in a.items() if k!='cells'}!={k:v for k,v in b.items() if k!='cells'} or set(a['cells'])!=set(b['cells']):raise ValueError('Changed module/state contract')
    for n,c in a['cells'].items():
        d=b['cells'][n]
        if {k:v for k,v in c.items() if k!='connections'}!={k:v for k,v in d.items() if k!='connections'}:raise ValueError('Changed primitive')
        for pin,bs in c['connections'].items():
            ds=d['connections'][pin]
            if bs==ds:continue
            if c['port_directions'][pin]!='input' or len(bs)!=1 or len(ds)!=1 or canonical.get(bs[0],bs[0])!=canonical.get(ds[0],ds[0]):raise ValueError('Rewrite is not an existing-state alias')
            if not (c['type'] in ('LUT1','LUT2','LUT3','LUT4') or c['type']=='ALU' and pin in ('I0','I1','I3') or c['type'].startswith('MUX2_LUT') and pin=='S0'):raise ValueError('Unmodeled modified endpoint')
    from ownpr_budgeted_restore import checked_wire
    w=checked_wire(wire,routes);rm=load_json(routes/'manifest.json')
    if rm['netlist_sha256']!=sha(placement/'packed.json') or rm['placement_sha256']!=sha(placement/'placement.json'):raise ValueError('Routes differ from candidate')
    clocks={c['connections']['CLK'][0] for c in a['cells'].values() if c['type'].startswith('DFF')}
    if len(clocks)!=1:raise ValueError('Multiple state clock domains')
    layout=load_json(placement/'placement.json');ioreg_links={row['iobuf']+'/I':n+'/Q' for n,row in layout.get('ioreg_clusters',{}).items()}
    captures={(r['instance'],r['pin']):r for r in load_json(sta/'sta_endpoint_audit.json')};drivers={bit:n+'/'+p for n,c in b['cells'].items() for p,bit in pins(c,'output')};inputs={n+'/'+p:bit for n,c in b['cells'].items() for p,bit in pins(c,'input')}
    measured={name:{tuple(s['node']):s['delays_by_selector'] for s in row['sinks']} for name,row in w['nets'].items()};result={};changes={};graphs={}
    for mode,early in (('setup',False),('hold',True)):
        graph=copy.deepcopy(model['early' if early else 'late']);count=0;rewired=0
        for e in graph['edges']:
            if e['kind']!='wire':continue
            bit=inputs[e['destination']];source=drivers[bit];name,pin=e['destination'].rsplit('/',1);cap=captures.get((name,pin));net='net_'+str(bit)
            if e['destination'] in ioreg_links:
                if source!=e['source'] or source!=ioreg_links[e['destination']] or e['delay']!=[0.,0.]:raise ValueError('Changed static IO register binding')
                delay=e['delay']
            elif bit in clocks:
                if source!=e['source']:raise ValueError('Changed clock driver')
                delay=e['delay']
            elif cap and len(cap['nodes'])==1:
                point=tuple(cap['nodes'][0]['node'])
                if net not in measured or point not in measured[net]:raise ValueError('Missing measured full-tree endpoint '+e['destination'])
                values=measured[net][point];delay=values[2][:2] if early else values[1][2:4];count+=1
            else:
                if e['source']!=source:raise ValueError('Changed dedicated data binding')
                delay=e['delay']
            rewired+=int(e['source']!=source);e.update(source=source,delay=list(delay))
        r=analyze(graph,model['hold_requirements' if early else 'setup_requirements'],early);result[mode]={k:r[k] for k in ('wns','tns','violations')};changes[mode]=dict(measured_wire_arcs=count,rewired_arcs=rewired)
        graphs[mode]=graph
    oldsetup=analyze(model['late'],model['setup_requirements']);oldhold=analyze(model['early'],model['hold_requirements'],True)
    result.update(baseline_setup={k:oldsetup[k] for k in ('wns','tns','violations')},baseline_hold={k:oldhold[k] for k in ('wns','tns','violations')},wire_updates=changes,
                  accepted_by_feedback=result['hold']['wns']>=.01 and result['setup']['wns']>oldsetup['wns']+.01,
                  source_sha256={str(f):sha(f) for f in (placement/'packed.json',placement/'placement.json',routes/'routes.json',routes/'clock_tree.json',wire/'wire_delays.json',model_dir/'model.json')},scope=__doc__,timing_signoff=False)
    return (result,graphs,model) if return_graphs else result


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for n in ('model','placement','routes','wire','out'):p.add_argument('--'+n,type=Path,required=True)
    a=p.parse_args();reserve(ROOT,2_000_000);start=time.monotonic();result=evaluate(a.model,a.placement,a.routes,a.wire);result['wall_seconds']=time.monotonic()-start
    with a.out.open('x') as f:json.dump(result,f,indent=2)
    print(json.dumps({k:result[k] for k in ('setup','hold','accepted_by_feedback','wall_seconds')},indent=2))
