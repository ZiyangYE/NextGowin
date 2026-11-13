"""Discover and certify a zero-area reassignment to an existing state replica."""
import argparse,json,time,shutil,sys
from pathlib import Path
from ownpr_device import ROOT,Device
from ownpr_image_storage import load_json,sha,copy_original
from ownpr_existing_state_alias import alias_groups,rewire,certify
from ownpr_critical_chain_move import locations
from workspace_budget import reserve


def discover(design,layout,ranking,arrivals):
    cells=design['modules']['top']['cells'];points=locations(layout);groups=alias_groups(design);byname={n:g for g in groups for n in g};result=[]
    distance=lambda a,b:abs(a[0]-b[0])+abs(a[1]-b[1])
    for row in ranking['ranked_nets']:
        parent=row['driver']['instance']
        if row['driver']['pin']!='Q' or parent not in byname or parent not in points:continue
        for replica in byname[parent]:
            if replica==parent or replica not in points:continue
            seeds=[arrivals.get(n+'/Q') for n in (parent,replica)]
            if any(v is None or any(x is None for x in v) for v in seeds):continue
            source_delta=max(seeds[1])-max(seeds[0]);sinks=[];gains=[];slacks=[];wires=[]
            for label in row['endpoints']:
                n,p=label.rsplit('/',1)
                if n not in points or cells[n]['type'] not in ('LUT1','LUT2','LUT3','LUT4'):continue
                witnesses=[(path['slack_ns'],arc['wire_delay_ns']) for path in ranking['traces'] for arc in path['ordinary_wires'] if arc['net']==row['net'] and arc['endpoint']==label]
                if not witnesses:continue
                slack,wire=min(witnesses);gain=distance(points[parent],points[n])-distance(points[replica],points[n])
                if slack>=0 or gain<2 or wire<.25 or source_delta>=wire:continue
                sinks.append((n,p));gains.append(gain);slacks.append(slack);wires.append(wire)
            if sinks:
                worst=min(slacks);critical=[i for i,s in enumerate(slacks) if round(s,3)==round(worst,3)]
                result.append(dict(parent=parent,replica=replica,sinks=sinks,worst_slack_ns=worst,distance_gain_tiles=sum(gains),
                    critical_distance_gain_tiles=sum(gains[i] for i in critical),critical_wire_ns=max(wires[i] for i in critical),
                    source_arrival_delta_ns=source_delta,maximum_reported_wire_ns=max(wires)))
    return sorted(result,key=lambda r:(round(r['worst_slack_ns'],3),-r['critical_wire_ns'],-r['critical_distance_gain_tiles'],r['source_arrival_delta_ns'],-r['distance_gain_tiles'],r['parent'],r['replica']))


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for n in ('base','sta','routes','arrival-feedback','out'):p.add_argument('--'+n,type=Path,required=True)
    p.add_argument('--rank',type=int,default=0);a=p.parse_args();start=time.monotonic();reserve(ROOT,30_000_000)
    from ownpr_critical_tree_rebuild import verify_feedback
    verify_feedback(a.base,a.routes,a.sta)
    from ownpr_cut_competition import context
    arrivals,arrival_info=context(a.arrival_feedback,a.base,a.sta)
    sys.path.insert(0,str(ROOT/'experiments'))
    from analyze_ownpr_critical_wires import analyze
    ranking=analyze(a.sta,a.base,a.routes,128);design=load_json(a.base/'packed.json');layout=load_json(a.base/'placement.json')
    options=discover(design,layout,ranking,arrivals)
    if not 0<=a.rank<len(options):raise ValueError('No existing state-alias candidate at rank')
    chosen=options[a.rank];after=rewire(design,chosen['parent'],chosen['replica'],chosen['sinks']);a.out.mkdir(exist_ok=False)
    proof=certify(design,after,chosen['parent'],chosen['replica'],a.out/'proof',120)
    from ownpr_critical_chain_move import apply as validate
    validate(after,layout,Device(),dict(sites={}))
    (a.out/'packed.json').write_text(json.dumps(after,indent=2));copy_original(a.base/'placement.json',a.out/'placement.json')
    sources={}
    for n in ('prepare_existing_state_alias.py','ownpr_existing_state_alias.py','ownpr_register_equivalence.py'):
        f=ROOT/'scripts'/n;shutil.copyfile(f,a.out/(n+'.txt'));sources[n]=sha(f)
    result=dict(status='placed',netlist_sha256=sha(a.out/'packed.json'),placement_sha256=sha(a.out/'placement.json'),
        source_netlist_sha256=sha(a.base/'packed.json'),source_placement_sha256=sha(a.base/'placement.json'),source_sta_sha256=sha(a.sta/'handoff_audit.json'),
        selected=chosen,proof=proof,arrival_feedback=arrival_info,source_helpers_sha256=sources,physical_placement_byte_identical=True,
        clocks_and_state_cells_unchanged=True,register_delta=0,lut_delta=0,timing_signoff=False,wall_seconds=time.monotonic()-start)
    for n,v in [('manifest.json',result),('candidates.json',options)]:
        with (a.out/n).open('x') as f:json.dump(v,f,indent=2)
    print(json.dumps(chosen,indent=2))


if __name__=='__main__':main()
