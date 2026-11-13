"""Prepare bounded whole-CFU moves including registers; fresh clock/STA required.

This does not reuse fixed-state required-time predictions for moved registers.
Full-graph slack weights rank geometry only, never establish timing benefit.
"""
import argparse,copy,json,math,shutil,sys,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from ownpr_image_storage import load_json,sha
from ownpr_model_reference import load_reference
from ownpr_global_relayout import units
from ownpr_device import Device
from ownpr_ff_configuration import compile_initial
from ownpr_mux_macros import validate as validate_mux
from ownpr_carry_macros import validate as validate_carry
from ownpr_bidir_timing import analyze
from workspace_budget import reserve


def prepare(placement,routes,wire,model,out,budget,radius,count,state_only):
    if not 1<=radius<=6 or not 1<=count<=32:raise ValueError('Invalid bounded move policy')
    if time.time()>load_json(budget)['deadline_epoch']-600:raise TimeoutError('Original attempt has insufficient final-STA reserve')
    reserve(ROOT,15_000_000);load_reference(model,placement,routes,wire)
    design=load_json(placement/'packed.json');old=load_json(placement/'placement.json');graph=load_json(model/'model.json');metrics=analyze(graph['late'],graph['setup_requirements']);rows,owner=units(design,old);device=Device()
    cells=design['modules']['top']['cells'];occupied={(r['row'],r['column']) for r in old['clusters'].values()}
    occupied.update((r['row'],r['column']) for r in old.get('arithmetic_clusters',{}).values())
    occupied.update((r['row'],r['column']+i) for r in old.get('hard_clusters',{}).values() for i in range(3))
    edges=[]
    for edge in graph['late']['edges']:
        if edge['kind']!='wire':continue
        a=owner.get(edge['source'].rsplit('/',1)[0]);b=owner.get(edge['destination'].rsplit('/',1)[0])
        if a is None or b is None or a==b:continue
        slack=min(metrics['slack'][edge['destination']]);weight=1+39*math.exp(max(-20.,min(0.,(metrics['wns']-slack)/.5)))
        edges.append((a,b,weight))
    positions={n:tuple(r['point']) for n,r in rows.items()};options=[]
    for key,unit in rows.items():
        if unit['kind']!='wide':continue
        states=[n for n in unit['members'] if cells[n]['type'].startswith('DFF')]
        if state_only and not states:continue
        incident=[e for e in edges if key in e[:2]]
        if not incident:continue
        origin=positions[key]
        def cost(point):
            return sum(w*(abs(point[0]-positions[b if a==key else a][0])+abs(point[1]-positions[b if a==key else a][1])) for a,b,w in incident)
        baseline=cost(origin);possibilities=[]
        for r in range(max(0,int(origin[0])-radius),min(device.rows,int(origin[0])+radius+1)):
            for c in range(max(0,int(origin[1])-radius),min(device.cols,int(origin[1])+radius+1)):
                point=r,c;distance=sum(abs(a-b) for a,b in zip(point,origin))
                if distance>radius or point in occupied or device.grid[r][c]!=17:continue
                density=sum(abs(r-p[0])+abs(c-p[1])<=2 for p in occupied)
                score=baseline-cost(point)-.05*sum(w for a,b,w in incident)*distance-.1*density
                if score>0:possibilities.append((-score,point))
        if possibilities:options.append((min(possibilities)[0],key,states,sorted(possibilities)))
    new=copy.deepcopy(old);chosen=[];reserved=set(occupied)
    for score,key,states,choices in sorted(options):
        target=next((point for _,point in choices if point not in reserved),None)
        if target is None:continue
        unit=rows[key];root=unit['root'];reserved.add(target)
        for name in unit['members']:
            for field in ('clusters','mux_clusters'):
                if name in new.get(field,{}):new[field][name].update(row=target[0],column=target[1])
        next(m for m in new['wide_macros'] if m['root']==root).update(row=target[0],column=target[1])
        chosen.append(dict(root=root,members=unit['members'],registers=states,previous=list(unit['point']),destination=list(target),geometry_ranking_gain=-score))
        if len(chosen)>=count:break
    if not chosen:raise ValueError('No legal positive-ranking whole-CFU move')
    # Same cell grouping, slots, INIT and control relations. The complete
    # downstream checkpoint must regenerate clock leaves/encoding and routes.
    compile_initial(design,new);mux=validate_mux(design,new,device);validate_carry(design,new,device)
    if old['fixed_io']!=new['fixed_io'] or old.get('hard_clusters')!=new.get('hard_clusters'):raise ValueError('Moved fixed interface/hard resource')
    out.mkdir(exist_ok=False);shutil.copyfile(placement/'packed.json',out/'packed.json');(out/'placement.json').write_text(json.dumps(new,indent=2));shutil.copyfile(__file__,out/'runner_source.py.txt')
    meta=load_json(placement/'manifest.json');meta.update(status='placed',placement_sha256=sha(out/'placement.json'),netlist_sha256=sha(out/'packed.json'),
        source_placement_sha256=sha(placement/'placement.json'),source_model_sha256=sha(model/'model.json'),moves=chosen,radius=radius,state_only=state_only,
        logical_netlist_byte_identical=True,clock_endpoints_unchanged=False,fresh_clock_route_and_full_sta_required=True,proxy_is_not_timing_gain=True,wide_macro_audit=mux,timing_signoff=False,
        source_sha256={str(f.resolve()):sha(f) for f in (placement/'packed.json',placement/'placement.json',model/'model.json',budget,Path(__file__))})
    (out/'manifest.json').write_text(json.dumps(meta,indent=2));print(json.dumps(dict(status='state_macro_candidate_prepared_not_validated',macros=len(chosen),registers=sum(len(x['registers']) for x in chosen),radius=radius)))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for n in ('placement','routes','wire','model','out','budget'):p.add_argument('--'+n,type=Path,required=True)
    p.add_argument('--radius',type=int,default=3);p.add_argument('--count',type=int,default=8);p.add_argument('--state-only',action='store_true');a=p.parse_args()
    prepare(a.placement,a.routes,a.wire,a.model,a.out,a.budget,a.radius,a.count,a.state_only)
