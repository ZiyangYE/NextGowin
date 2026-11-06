"""Timing-weighted co-packing of ordinary LUT/FF units with common controls.

Full incident D/Q/CE/SR span is a placement proxy. Clock retargeting, actual
whole-tree routing, complete I/O STA and native encoding remain mandatory.
"""
import argparse,copy,json,shutil,time
from collections import defaultdict
from pathlib import Path
from ownpr_device import ROOT,Device
from ownpr_image_storage import load_json,sha,copy_original
from ownpr_shared_ff_packing import calibration,constants,signature,compile_sites
from ownpr_critical_chain_move import locations,exclusive_tiles,apply as validate


def propose(design,layout,device,priorities,count=64,radius=24,protected=()):
    if not 1<=count<=256 or not 1<=radius<=64:raise ValueError('Invalid shared pack bounds')
    cells=design['modules']['top']['cells'];const=constants(design);excluded=exclusive_tiles(layout);points=locations(layout)
    placed=copy.deepcopy(layout);clusters=placed['clusters'];owners={(r['row'],r['column'],r['slot']):n for n,r in clusters.items()};protected=set(protected)
    eligible={};by_signature=defaultdict(list)
    for n,row in clusters.items():
        r,c,s=row['row'],row['column'],row['slot'];ff=row.get('ff')
        if not ff or row.get('wide_ff_root') or (r,c) in excluded or device.grid[r][c]!=17 or s not in (0,2,4):continue
        other=owners.get((r,c,s+1))
        if other and clusters[other].get('ff'):continue  # Preserve an existing shared pair.
        key=signature(cells[ff],const)
        if key is None:continue
        if cells[n]['type'] not in ('LUT1','LUT2','LUT3','LUT4') or cells[ff]['connections']['D']!=cells[n]['connections']['F']:raise ValueError('Invalid ordinary LUT/FF pair')
        eligible[n]=key;by_signature[key].append(n)
    drivers={b:n for n,c in cells.items() for p,bs in c['connections'].items() if c['port_directions'][p]=='output' for b in bs};edges=[];incident=defaultdict(set)
    for n,c in cells.items():
        for pin,bs in c['connections'].items():
            if c['port_directions'][pin]!='input' or pin.startswith('CLK') or pin=='CIN':continue
            for i,b in enumerate(bs):
                d=drivers.get(b)
                if d not in points or n not in points or d==n:continue
                label=pin if len(bs)==1 else f'{pin}[{i}]';crit=priorities.get((n,label),priorities.get((n,pin),0.));index=len(edges)
                edges.append((d,n,1.+49.*crit**2));incident[d].add(index);incident[n].add(index)
    def cost(indices,loc):
        return sum(w*(abs(loc.get(a,points[a])[0]-loc.get(b,points[b])[0])+abs(loc.get(a,points[a])[1]-loc.get(b,points[b])[1])) for a,b,w in (edges[i] for i in indices))
    # Bounding targets limits search work, not correctness or a global optimum.
    options=[]
    for n,key in eligible.items():
        ff=clusters[n]['ff'];ids=incident[n]|incident[ff]
        targets=[]
        for anchor in by_signature[key]:
            if anchor==n:continue
            row=clusters[anchor];dest=row['row'],row['column'];span=abs(dest[0]-points[n][0])+abs(dest[1]-points[n][1])
            if span>radius:continue
            targets.append((cost(ids,{n:dest,ff:dest}),anchor))
        options.extend((n,anchor) for _,anchor in sorted(targets)[:48])
    moves=[];locked=set();initial_cost=cost(range(len(edges)),{})
    def score(n,anchor):
        if n in locked or anchor in locked:return None
        row=clusters[n];target=clusters[anchor];old=tuple(row[k] for k in ('row','column','slot'));dest=target['row'],target['column'],target['slot']+1
        displaced=owners.get(dest)
        if displaced is not None and (displaced in protected or displaced in locked or clusters[displaced].get('ff') or clusters[displaced].get('wide_ff_root') or cells[displaced]['type'] not in ('LUT1','LUT2','LUT3','LUT4')):return None
        ff=row['ff'];changed={n:dest[:2],ff:dest[:2]};ids=set(incident[n]|incident[ff])
        if displaced:
            changed[displaced]=old[:2];ids.update(incident[displaced])
        before,after=cost(ids,{}),cost(ids,changed);gain=before-after
        if gain<=1e-6:return None
        return dict(lut=n,ff=ff,anchor_lut=anchor,anchor_ff=target['ff'],old_site=list(old),new_site=list(dest),displaced_lut=displaced,
            weighted_incident_before=before,weighted_incident_after=after,gain=gain)
    # Re-score each proposed move after earlier decisions; shared anchors are
    # locked so their newly occupied control groups cannot be split later.
    ranked=[]
    for n,anchor in options:
        item=score(n,anchor)
        if item:ranked.append((-item['gain'],n,anchor))
    for _,n,anchor in sorted(ranked):
        if len(moves)>=count:break
        item=score(n,anchor)
        if item is None:continue
        old,new=tuple(item['old_site']),tuple(item['new_site']);displaced=item['displaced_lut'];ff=item['ff']
        del owners[old]
        if displaced:
            owners[old]=displaced;clusters[displaced].update(row=old[0],column=old[1],slot=old[2]);points[displaced]=old[:2];locked.add(displaced)
        owners[new]=n;clusters[n].update(row=new[0],column=new[1],slot=new[2]);points[n]=points[ff]=new[:2];locked.update((n,anchor));moves.append(item)
    if not moves:raise ValueError('No compatible shared pair improves the joint incident objective')
    placed['shared_ff_calibration']=calibration()['admission_sha256'];groups,admission=compile_sites(design,placed,device)
    final_cost=cost(range(len(edges)),{})
    if final_cost>=initial_cost or abs((initial_cost-final_cost)-sum(m['gain'] for m in moves))>1e-5:raise ValueError('Joint incident cost accounting differs')
    return placed,dict(moves=moves,eligible_units=len(eligible),control_signatures=len(by_signature),target_pairs=len(options),
        shared_pairs=len(admission['records']),weighted_span_before=initial_cost,weighted_span_after=final_cost,
        scope='Bounded greedy packing with exact current incident-span updates, not measured timing or global placement optimality. All D/Q/CE/SR edges included; clocks rebuilt separately.')


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for key in ('base','routes','sta','pin-feedback','out'):p.add_argument('--'+key,type=Path,required=True)
    p.add_argument('--count',type=int,default=64);p.add_argument('--radius',type=int,default=24);p.add_argument('--target-mhz',type=float,default=90);p.add_argument('--global-relayout',action='store_true');a=p.parse_args()
    from ownpr_critical_tree_rebuild import verify_feedback
    from ownpr_pin_feedback import load,placement_priorities
    from workspace_budget import reserve
    reserve(ROOT,100_000_000);start=time.monotonic();verify_feedback(a.base,a.routes,a.sta);budgets,feedback=load(a.pin_feedback,a.sta)
    design=load_json(a.base/'packed.json');original=load_json(a.base/'placement.json');device=Device()
    priorities=placement_priorities(design,budgets)
    from ownpr_feedback_transport import protected_placement_cells
    from ownpr_target_protection import from_capture
    window,conversion=from_capture(a.sta,a.target_mhz,.5);protected,protection=protected_placement_cells(design,original,budgets,window)
    placed,plan=propose(design,original,device,priorities,a.count,a.radius,protected)
    plan['incidental_displacement_protection']=dict(protection,target_conversion=conversion)
    if a.global_relayout:
        from ownpr_global_relayout import units,edges,relax,legalize
        rows,owner=units(design,placed);edge_rows=edges(design,owner,priorities)
        targets,continuous=relax(rows,edge_rows,device)
        placed,legal=legalize(design,placed,rows,targets,edge_rows,device)
        plan['global_placement']=dict(continuous=continuous,legal=legal,shared_units=sum(r['kind']=='shared' for r in rows.values()))
        plan['moves_scope']='Shared grouping stage only; final sites are in placement.json after global relayout'
    placed=validate(design,placed,device,dict(sites={}))
    if placed['fixed_io']!=original['fixed_io'] or placed.get('hard_clusters')!=original.get('hard_clusters'):raise ValueError('Fixed physical boundary changed')
    a.out.mkdir(exist_ok=False);copy_original(a.base/'packed.json',a.out/'packed.json');(a.out/'placement.json').write_text(json.dumps(placed,indent=2));(a.out/'plan.json').write_text(json.dumps(plan,indent=2))
    if sha(a.out/'packed.json')!=sha(a.base/'packed.json'):raise ValueError('Shared packing changed logical bytes')
    sources=[Path(__file__),ROOT/'scripts/ownpr_shared_ff_packing.py',ROOT/'scripts/ownpr_ff_configuration.py',ROOT/'scripts/ownpr_shared_ff_calibration.py',ROOT/'scripts/ownpr_target_protection.py',ROOT/'scripts/ownpr_feedback_transport.py']
    if a.global_relayout:sources.append(ROOT/'scripts/ownpr_global_relayout.py')
    for s in sources:shutil.copyfile(s,a.out/(s.name+'.txt'))
    result=dict(status='placed',netlist_sha256=sha(a.out/'packed.json'),placement_sha256=sha(a.out/'placement.json'),source_netlist_sha256=sha(a.base/'packed.json'),source_placement_sha256=sha(a.base/'placement.json'),
        logical_netlist_byte_identical=True,clock_replan_required=True,interface_cycles_changed=False,fixed_io_changed=False,global_relayout=a.global_relayout,plan=plan,pin_feedback=feedback,wall_seconds=time.monotonic()-start,
        source_sha256={s.name:sha(a.out/(s.name+'.txt')) for s in sources},vendor_pnr_executed=False,timing_signoff=False)
    (a.out/'manifest.json').write_text(json.dumps(result,indent=2));print(json.dumps({k:v for k,v in plan.items() if k!='moves'},indent=2))


if __name__=='__main__':main()
