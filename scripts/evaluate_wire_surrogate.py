"""Grouped-net pilot of placement-available wire features; no signoff use."""
import argparse,hashlib,json
from pathlib import Path
import numpy as np
from extract_measured_route_samples import extract
from ownpr_image_storage import load_json
from workspace_budget import reserve
from ownpr_device import ROOT

FEATURES=['dx','dy','sqrt_distance','log_fanout','span_x','span_y','distance_log_fanout','span_log_fanout']
def features(rows):
    result=[]
    for r in rows:
        distance=r['dx']+r['dy'];fanout=np.log1p(r['fanout']);span=r['span_x']+r['span_y']
        result.append([r['dx'],r['dy'],np.sqrt(distance),fanout,r['span_x'],r['span_y'],distance*fanout,span*fanout])
    return np.asarray(result,dtype=float)
def metrics(actual,predicted):
    errors=np.abs(actual-predicted)
    return dict(samples=len(errors),mae_ns=float(errors.mean()),p95_absolute_error_ns=float(np.quantile(errors,.95)),max_absolute_error_ns=float(errors.max()))
def main():
    p=argparse.ArgumentParser();p.add_argument('--base',type=Path,required=True);p.add_argument('--candidate-samples',type=Path,required=True);p.add_argument('--out',type=Path,required=True);a=p.parse_args();reserve(ROOT,30_000_000)
    base=extract(a.base/'route',a.base/'export/wire');candidate=load_json(a.candidate_samples)
    # Reserve both changed trees entirely; no sibling sinks leak into training.
    changed={'net_1862','net_9746'}
    held=lambda name:name in changed or int(hashlib.sha256(name.encode()).hexdigest()[:8],16)%5==0
    train=[r for r in base['samples'] if not held(r['net'])];test=[r for r in base['samples'] if held(r['net'])]
    x=features(train);mean=x.mean(axis=0);scale=x.std(axis=0);scale[scale<1e-9]=1
    transform=lambda rows:np.column_stack([np.ones(len(rows)),(features(rows)-mean)/scale])
    x=transform(train);y=np.array([r['setup_wire_ns'] for r in train]);penalty=np.eye(x.shape[1]);penalty[0,0]=0
    weights=np.linalg.solve(x.T@x+1.*penalty,x.T@y)
    predict=lambda rows:np.maximum(0.,transform(rows)@weights)
    moved=[r for r in candidate['samples'] if r['net'] in changed]
    evaluation=metrics(np.array([r['setup_wire_ns'] for r in test]),predict(test));movement=metrics(np.array([r['setup_wire_ns'] for r in moved]),predict(moved))
    report=dict(status='single_design_grouped_net_pilot',signoff_allowed=False,production_ranking_enabled=False,cross_design_validated=False,features=FEATURES,post_route_pip_count_used=False,train_samples=len(train),train_nets=len({r['net'] for r in train}),test_nets=len({r['net'] for r in test}),withheld_changed_nets=sorted(changed),validation=evaluation,changed_tree_validation=movement,mean=mean.tolist(),scale=scale.tolist(),weights=weights.tolist(),base_provenance=base['source_sha256'],candidate_provenance=candidate['source_sha256'])
    with a.out.open('x') as stream:json.dump(report,stream,indent=2)
    print(json.dumps(dict(validation=evaluation,changed_tree_validation=movement)))
if __name__=='__main__':main()
