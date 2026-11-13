"""Partition critical launch loads and jointly place ordinary LUT/FF units.

Paths select several receiver regions per launch, rather than one cone per
launch. Paired LUT/FF units can move together in a bounded wider corridor.
Hard blocks, carry and wide macros remain physical anchors in this version.
Geometry ranks candidates only; induction and complete routed STA are required.
"""
import argparse,copy,json,shutil,time
from collections import defaultdict
from pathlib import Path
from ownpr_device import ROOT,Device
from ownpr_image_storage import load_json,read_text,sha
from ownpr_regional_replication import replicate,LUTS
from ownpr_existing_state_alias import admissible
from ownpr_critical_chain_move import locations,exclusive_tiles,apply as validate
from ownpr_register_equivalence import prove_replicas
from routing_path_diagnosis import diagnose
from workspace_budget import reserve
from ownpr_state_safety import protected_cells


def partitions(design,layout,paths,reverse,window=.8,max_launches=4,max_copies=6):
    """Greedy complete-link receiver groups; names carry no benchmark meaning."""
    cells=design['modules']['top']['cells'];points=locations(layout)
    paired={r['ff']:n for n,r in layout['clusters'].items() if r.get('ff') and not r.get('wide_ff_root') and not r.get('independent_ff_input') and cells[n]['type'] in LUTS}
    protected=protected_cells(design)
    worst=min(p['slack_ns'] for p in paths);groups=defaultdict(dict)
    for p in paths:
        if p['slack_ns']>worst+window:continue
        launch=reverse[p['source']]
        if launch not in paired or launch in protected or paired[launch] in protected or not admissible(cells[launch]) or set(cells[launch].get('parameters',{}))-{'INIT'}:continue
        bit=cells[launch]['connections']['Q']
        for e in p['edges']:
            src,_,_=e['source'].rpartition('/');dst,_,pin=e['destination'].rpartition('/');n=reverse[dst]
            if reverse[src]!=launch or cells[n]['connections'].get(pin)!=bit:continue
            typ=cells[n]['type']
            if not (typ in LUTS and pin.startswith('I') or typ=='ALU' and pin in ('I0','I1','I3') or typ.startswith('MUX2_LUT') and pin=='S0'):continue
            if n not in points:continue
            row=groups[launch].setdefault((n,pin),dict(paths=set(),worst_slack_ns=p['slack_ns'],wire_ns=0.))
            row['paths'].add(p['path']);row['wire_ns']=max(row['wire_ns'],e['delay_ns']);row['worst_slack_ns']=min(row['worst_slack_ns'],p['slack_ns'])
    ranked=sorted(groups,key=lambda n:(min(r['worst_slack_ns'] for r in groups[n].values()),-sum(len(r['paths'])*r['wire_ns'] for r in groups[n].values()),n))[:max_launches]
    result=[]
    for launch in ranked:
        pending=sorted(groups[launch],key=lambda s:(groups[launch][s]['worst_slack_ns'],-groups[launch][s]['wire_ns'],s))
        per_launch=0
        while pending and per_launch<2 and len(result)<max_copies:
            anchor=pending.pop(0);sinks=[anchor]
            for s in list(pending):
                if all(sum(abs(a-b) for a,b in zip(points[s[0]],points[x[0]]))<=6 for x in sinks):sinks.append(s);pending.remove(s)
            rows=[groups[launch][s] for s in sinks]
            result.append(dict(source=launch,sinks=sinks,covered_paths=sorted(set().union(*(r['paths'] for r in rows))),worst_slack_ns=min(r['worst_slack_ns'] for r in rows),maximum_wire_ns=max(r['wire_ns'] for r in rows)))
            per_launch+=1
    return result


def relax(design,layout,device,paths,reverse,origins,radius=12,count=48,passes=2):
    """Coordinate descent on measured-path-weighted incident-net geometry.

    Existing shared FF control groups and all macro CFUs are excluded. New
    pairs use independently calibrated FF slots. Added D/control loads are
    present in the objective, including those absent from old critical paths.
    """
    cells=design['modules']['top']['cells'];placed=copy.deepcopy(layout);points=locations(placed);excluded=exclusive_tiles(placed)
    protected=protected_cells(design)
    worst=min(p['slack_ns'] for p in paths);weights={};hot=defaultdict(float)
    for p in paths:
        priority=max(0.,1-(p['slack_ns']-worst)/2.)**2
        for e in p['edges']:
            src,_,_=e['source'].rpartition('/');dst,_,pin=e['destination'].rpartition('/');a,b=reverse[src],reverse[dst]
            key=a,b,pin;weights[key]=max(weights.get(key,1.),1+20*priority*max(.1,e['delay_ns']))
            hot[a]=max(hot[a],priority);hot[b]=max(hot[b],priority)
    drivers={b:n for n,c in cells.items() for p,bs in c['connections'].items() if c['port_directions'][p]=='output' for b in bs}
    edges=[];incident=defaultdict(list)
    for n,c in cells.items():
        for pin,bs in c['connections'].items():
            if c['port_directions'][pin]!='input' or 'CLK' in pin or pin=='CIN':continue
            for b in bs:
                a=drivers.get(b)
                if a not in points or n not in points or a==n:continue
                w=weights.get((origins.get(a,a),origins.get(n,n),pin),1.)
                index=len(edges);edges.append((a,n,w));incident[a].append(index);incident[n].append(index)
    occupied={(r['row'],r['column'],r['slot']):n for n,r in placed['clusters'].items()};controls=defaultdict(list)
    for n,r in placed['clusters'].items():
        if r.get('ff'):controls[r['row'],r['column'],r['slot']//2].append(n)
    units=[]
    for n,r in placed['clusters'].items():
        ff=r.get('ff');members={n}|({ff} if ff else set())
        if members&protected:continue
        if (r['row'],r['column']) in excluded or r.get('wide_ff_root') or cells[n]['type'] not in LUTS:continue
        if ff and (r['slot'] not in (0,2,4) or len(controls[r['row'],r['column'],r['slot']//2])!=1 or not admissible(cells[ff]) or set(cells[ff].get('parameters',{}))-{'INIT'}):continue
        priority=max(hot.get(origins.get(x,x),0.) for x in members)
        if priority<=0:continue
        ids=sorted(set().union(*(set(incident[x]) for x in members)))
        units.append((-priority,-sum(edges[i][2] for i in ids),n,members,ids))
    units=sorted(units,key=lambda r:r[:3])[:count];moves=[]
    for pass_index in range(passes):
        for _,_,n,members,ids in units:
            row=placed['clusters'][n];ff=row.get('ff');old=(row['row'],row['column'],row['slot']);origin=layout['clusters'][n]
            def cost(point):
                return sum(w*(abs((point if a in members else points[a])[0]-(point if b in members else points[b])[0])+abs((point if a in members else points[a])[1]-(point if b in members else points[b])[1])) for a,b,w in (edges[i] for i in ids))
            before=cost(old[:2]);best=(before,0,old)
            for r in range(max(0,origin['row']-radius),min(device.rows,origin['row']+radius+1)):
                for c in range(max(0,origin['column']-radius),min(device.cols,origin['column']+radius+1)):
                    if device.grid[r][c]!=17 or (r,c) in excluded:continue
                    value=cost((r,c))
                    if value>=before-.001:continue
                    for slot in ((0,2,4) if ff else range(8)):
                        if (r,c,slot) in occupied and occupied[r,c,slot]!=n:continue
                        if ff and any(x!=n for x in controls.get((r,c,slot//2),[])):continue
                        key=value,abs(r-old[0])+abs(c-old[1])+int(slot!=old[2]),(r,c,slot)
                        if key<best:best=key
            if best[2]==old:continue
            target=best[2];del occupied[old];occupied[target]=n
            if ff:
                controls[old[0],old[1],old[2]//2].remove(n);controls[target[0],target[1],target[2]//2].append(n)
            row.update(row=target[0],column=target[1],slot=target[2])
            for member in members:points[member]=target[:2]
            moves.append(dict(lut=n,ff=ff,old_site=old,new_site=target,geometric_cost_before=before,geometric_cost_after=best[0],pass_index=pass_index))
    validate(design,placed,device,dict(sites={}))
    return placed,moves


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for n in ('base','sta','routes','out'):p.add_argument('--'+n,type=Path,required=True)
    p.add_argument('--window',type=float,default=.8);p.add_argument('--copies',type=int,default=6);p.add_argument('--launches',type=int,default=4);p.add_argument('--move-count',type=int,default=0);p.add_argument('--radius',type=int,default=12)
    p.add_argument('--no-displacement',action='store_true',default=True,help='Use free replica slots only (default)')
    p.add_argument('--allow-displacement',dest='no_displacement',action='store_false',help='Experimental geometric legalization; not the qualified policy');a=p.parse_args()
    if not 0<a.window<=3 or not 1<=a.copies<=8 or not 1<=a.launches<=4 or not 0<=a.move_count<=96 or not 1<=a.radius<=24:p.error('Invalid bounded search')
    reserve(ROOT,100_000_000);start=time.monotonic()
    from ownpr_critical_tree_rebuild import verify_feedback
    verify_feedback(a.base,a.routes,a.sta)
    old=load_json(a.base/'packed.json');layout=load_json(a.base/'placement.json');paths=diagnose(read_text(a.sta/'probe.tr'));reverse={v:k for k,v in load_json(a.sta/'name_map.json')['instances'].items()};device=Device()
    choices=partitions(old,layout,paths,reverse,a.window,a.launches,a.copies)
    new,placed=old,layout;aliases={};origins={};changes=[];rejections=[]
    for choice in choices:
        try:nd,np,change=replicate(new,placed,device,choice['source'],choice['sinks'],depth=1,max_registers=1,allowed_registers={choice['source']},cluster_region=not a.no_displacement,placement_radius=12 if a.no_displacement else 5)
        except ValueError as e:rejections.append(dict(choice,error=str(e)));continue
        aliases.update(change['register_replicas']);origins.update(change['register_replicas'])
        origins.update({r['replica']:r['original'] for r in change['placements']});new,placed=nd,np;changes.append(dict(choice,change=change))
    if not aliases:raise ValueError('No supported critical launch partition')
    if a.move_count:placed,moves=relax(new,placed,device,paths,reverse,origins,a.radius,a.move_count)
    else:moves=[]
    if a.no_displacement and not a.move_count and any(placed['clusters'][n]!=r for n,r in layout['clusters'].items()):raise ValueError('No-displacement ablation moved an original unit')
    validate(new,placed,device,dict(sites={}))
    a.out.mkdir(exist_ok=False)
    proof=prove_replicas(old,new,aliases,a.out/'proof',180)
    for name,data in [('packed.json',new),('placement.json',placed),('partitions.json',changes),('moves.json',moves),('proof.json',proof),('rejections.json',rejections)]:
        (a.out/name).write_text(json.dumps(data,indent=2))
    sources=[Path(__file__),ROOT/'scripts/ownpr_regional_replication.py',ROOT/'scripts/ownpr_register_equivalence.py',ROOT/'scripts/ownpr_macro_legalize.py',ROOT/'scripts/ownpr_state_safety.py']
    for source in sources:shutil.copyfile(source,a.out/(source.name+'.txt'))
    manifest=dict(status='placed',netlist_sha256=sha(a.out/'packed.json'),placement_sha256=sha(a.out/'placement.json'),source_netlist_sha256=sha(a.base/'packed.json'),source_placement_sha256=sha(a.base/'placement.json'),source_sta_sha256=sha(a.sta/'probe.tr'),partitions=len(changes),register_replicas=aliases,moves=len(moves),clock_replan_required=True,interface_cycles_changed=False,fixed_io_changed=placed['fixed_io']!=layout['fixed_io'],vendor_pnr_executed=False,physical_legality_complete=False,timing_signoff=False,proof=proof,wall_seconds=time.monotonic()-start,source_sha256={source.name:sha(source) for source in sources})
    if manifest['fixed_io_changed']:raise ValueError('Changed package pins')
    (a.out/'manifest.json').write_text(json.dumps(manifest,indent=2));print(json.dumps({k:manifest[k] for k in ('status','partitions','moves','register_replicas','wall_seconds')},indent=2))


if __name__=='__main__':main()
