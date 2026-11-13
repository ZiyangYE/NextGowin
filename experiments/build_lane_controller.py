"""Build explicitly permuted memory-boundary proof views, without proving them."""
import argparse,json,sys,time,shutil
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from ownpr_image_storage import load_json,sha
from workspace_budget import reserve
from compositional_memory_cut import propose,initial_relation,abstract
from formal_memory_lanes import propose_lanes
from formal_padding_projection import certify,project


def main():
    p=argparse.ArgumentParser();p.add_argument('--pair',type=Path,required=True);p.add_argument('--out',type=Path,required=True);p.add_argument('--budget',type=Path,required=True);p.add_argument('--project-padding',action='store_true');a=p.parse_args()
    reserve(ROOT,100_000_000);start=time.time();out=a.out.resolve();out.mkdir(exist_ok=False);pair=a.pair.resolve();meta=load_json(pair/'manifest.json');budget=load_json(a.budget)
    r=dict(status='building_lane_controller',full_source_equivalence_proved=False,production_model_admission=False,original_budget=budget,source_manifest_sha256=sha(pair/'manifest.json'))
    def save():r['wall_seconds']=time.time()-start;(out/'manifest.json').write_text(json.dumps(r,indent=2))
    save();shutil.copyfile(__file__,out/'runner_source.py.txt')
    try:
        for role in ('gold','gate'):
            if sha(pair/(role+'_memory.json'))!=meta[role]['sha256']:raise ValueError('Array source changed')
        gold=load_json(pair/'gold_memory.json');gate=load_json(pair/'gate_memory.json');top=meta['top']
        groups=propose_lanes(gold,gate,top,propose(gold,gate,top,load_json(pair/'bridge.json')))
        r['groups']=groups;r['initial_storage_relation']=initial_relation(gold,gate,top,groups);save()
        if a.project_padding:
            r['padding_projection']=certify(gate,top,groups)
            r['padding_projection']['original_gate_sha256']=meta['gate']['sha256']
            r['transformation_sources']={n:sha(ROOT/'scripts'/n) for n in ('compositional_memory_cut.py','formal_memory_lanes.py','formal_padding_observability.py','formal_padding_projection.py')};save()
        for role,d in [('gold',gold),('gate',gate)]:
            cut,info=abstract(d,top,groups,role)
            if a.project_padding:
                cut,projection=project(cut,top,r['padding_projection'],role);info['padding_projection']=projection
            path=out/(role+'_cut.json');path.write_text(json.dumps(cut,separators=(',',':')))
            r[role]=dict(info,sha256=sha(path));save()
        if time.time()>budget['deadline_epoch']-120:raise TimeoutError('Original round deadline')
        r['status']='lane_controller_built_not_proved';save()
        print(json.dumps(dict(status=r['status'],wall_seconds=r['wall_seconds'],nonidentity=[dict(memory=g['gold'],pairs=s['lane_pairs'],constants=s['compacted_constant_proposals']) for g in groups for s in g['spans'] if s['lane_pairs']!=[[s['logical_low']+i,i] for i in range(s['width'])]])))
    except Exception as e:r.update(status='lane_controller_rejected',error=repr(e));save();raise


if __name__=='__main__':main()
