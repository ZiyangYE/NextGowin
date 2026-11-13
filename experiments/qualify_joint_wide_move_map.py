"""Same exact joint operator across a checkpoint cohort; no routed performance claim."""
import argparse,copy,json,sys,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from ownpr_image_storage import load_json,sha
from ownpr_wide_trust_region import movable,translate_recompile
from ownpr_general_wide_recompile import info
from ownpr_existing_wide_remap import macro_truth
from ownpr_critical_chain_move import locations
from ownpr_mux_macros import validate
from ownpr_ff_configuration import compile_initial
from ownpr_device import Device
from native_boundary_cec import prove
from workspace_budget import reserve

def main():
    p=argparse.ArgumentParser();p.add_argument('--checkpoint',type=Path,required=True);p.add_argument('--out',type=Path,required=True);a=p.parse_args()
    reserve(ROOT,50000000);a.out.mkdir(exist_ok=False);device=Device();results=[];started=time.time()
    for record in load_json(a.checkpoint)['designs']:
        for f,h in record['selected_source_sha256'].items():
            if sha(ROOT/f)!=h:raise ValueError('Changed source evidence')
        source=ROOT/record['selected']['placement'];d=load_json(source/'packed.json');l=load_json(source/'placement.json')
        occupied=set(locations(l).values());occupied.update((r['row'],r['column']+i) for r in l.get('hard_clusters',{}).values() for i in range(3))
        accepted=[];reject=[]
        for root,(spec,members) in sorted(movable(d,l).items()):
            if len(accepted)>=3:break
            row=next(m for m in l['wide_macros'] if m['root']==root);origin=(row['row'],row['column'])
            choices=[(r,c) for r in range(max(0,origin[0]-3),min(device.rows,origin[0]+4)) for c in range(max(0,origin[1]-3),min(device.cols,origin[1]+4)) if (r,c) not in occupied and device.grid[r][c]==17]
            if not choices:continue
            point=min(choices,key=lambda q:(abs(q[0]-origin[0])+abs(q[1]-origin[1]),q))
            try:
                _,boundary,members=info(d,l,root);support=macro_truth(d,root,boundary,members)['cut_bits']
                for promoted in support:
                    try:nd,nl,change=translate_recompile(d,l,root,point,promoted,support)
                    except ValueError:continue
                    validate(nd,nl,device);compile_initial(nd,nl)
                    proof=prove(d,nd,a.out/record['design']/('case_'+str(len(accepted))),60)
                    if not proof or not all(x['cec']['equivalent'] for x in proof):raise ValueError('Boundary proof missing')
                    accepted.append(dict(change=change,proof=proof));break
            except ValueError as e:reject.append(dict(root=root,error=str(e)))
        results.append(dict(design=record['design'],source_sha256=sha(source/'packed.json'),placement_sha256=sha(source/'placement.json'),cases=accepted,rejections=reject))
    result=dict(status='structural_cohort_qualification',designs=results,routed_performance_verified=False,wall_seconds=time.time()-started)
    (a.out/'summary.json').write_text(json.dumps(result,indent=2));print([(r['design'],len(r['cases'])) for r in results])

if __name__=='__main__':main()
