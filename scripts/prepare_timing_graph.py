"""Serialize real mapped LUT DAGs and an independent vectorized timing oracle."""
import argparse
import json
import math
from pathlib import Path
import struct
import numpy as np
from gowin_blif import read_blif
from constrained_mapping import constraints
from run_abc_reproduction import ROOT,sha

INF=1<<60


def build(network,configs,delay_ps,seed=42,boundaries=None):
    if not 1<=configs<=4096 or delay_ps<=0:raise ValueError('Invalid snapshot count or delay')
    names=list(network['inputs'])+list(network['topological']);indices={n:i for i,n in enumerate(names)}
    n=len(names)
    if n==0 or n*configs>50000000:raise ValueError('Timing snapshot storage limit exceeded')
    nodes={r['output']:r for r in network['nodes']};fanin=[[] for _ in names];fanout=[[] for _ in names]
    delays=np.zeros(n,dtype='<i8');levels=np.zeros(n,dtype='<u4')
    for name in network['topological']:
        node=nodes[name];k=len(node['inputs']);truth=node['truth'];idx=indices[name]
        active=[p for i,p in enumerate(node['inputs']) if any(((truth>>x)&1)!=((truth>>(x^(1<<i)))&1) for x in range(1<<k))]
        parents=list(dict.fromkeys(indices[p] for p in active));fanin[idx]=parents
        if parents:
            delays[idx]=delay_ps;levels[idx]=max(int(levels[p]) for p in parents)+1
            for parent in parents:fanout[parent].append(idx)
    def csr(rows):
        offsets=[0];edges=[]
        for row in rows:edges.extend(row);offsets.append(len(edges))
        return np.asarray(offsets,dtype='<u4'),np.asarray(edges,dtype='<u4')
    in_offsets,in_edges=csr(fanin);out_offsets,out_edges=csr(fanout)
    groups=[[] for _ in range(int(max(levels))+1)]
    for node,level in enumerate(levels):groups[int(level)].append(node)
    level_offsets,level_nodes=csr(groups)
    rng=np.random.default_rng(seed);arrivals=np.zeros((configs,n),dtype='<i8');required=np.full((configs,n),INF,dtype='<i8')
    outputs=np.asarray([indices[p] for p in network['outputs']],dtype='<u4')
    if boundaries is not None:
        if configs!=1:raise ValueError('Explicit boundary constraints require exactly one snapshot')
        arr,req=boundaries
        for name,value in arr.items():arrivals[0,indices[name]]=math.ceil(value)
        for name,value in req.items():required[0,indices[name]]=math.floor(value)
    else:
        arrivals[:,:len(network['inputs'])]=rng.integers(0,2001,size=(configs,len(network['inputs'])))
        base=int(max(levels))*delay_ps+2000
        for idx in outputs:required[:,idx]=base+rng.integers(-1000,1001,size=configs)
    if np.any(abs(arrivals)>=1<<50) or np.any((required!=INF)&(abs(required)>=1<<50)):
        raise ValueError('Boundary time outside safe integer range')
    expected_arrivals=arrivals.copy();expected_required=required.copy()
    # Different traversal from the GPU level order; vectorize over snapshots.
    for node in range(n):
        if fanin[node]:expected_arrivals[:,node]=expected_arrivals[:,fanin[node]].max(axis=1)+delays[node]
    for node in reversed(range(n)):
        if fanout[node]:
            sinks=fanout[node];values=expected_required[:,sinks]
            candidate=np.where(values==INF,INF,values-delays[sinks]).min(axis=1)
            expected_required[:,node]=np.minimum(expected_required[:,node],candidate)
    arrays=[delays,in_offsets,in_edges,out_offsets,out_edges,level_offsets,level_nodes,
            arrivals,required,expected_arrivals,expected_required]
    return dict(names=names,levels=levels,outputs=outputs,arrays=arrays,
        header=[n,len(in_edges),len(groups),configs,len(network['inputs'])])


def write(path,data):
    with path.open('wb') as stream:
        stream.write(b'NGSTA001');stream.write(struct.pack('<5I',*data['header']))
        for array in data['arrays']:stream.write(array.tobytes(order='C'))


def main():
    ap=argparse.ArgumentParser();ap.add_argument('input',type=Path)
    ap.add_argument('--configs',type=int,default=1);ap.add_argument('--seed',type=int,default=42)
    ap.add_argument('--delay-ps',type=int,default=521);ap.add_argument('--constraints',type=Path)
    ap.add_argument('--out',type=Path,required=True);args=ap.parse_args()
    out=args.out.resolve();out.mkdir(parents=True,exist_ok=False)
    network=read_blif(args.input,max_inputs=6)
    boundaries=constraints(json.loads(args.constraints.read_text()),network) if args.constraints else None
    data=build(network,args.configs,args.delay_ps,args.seed,boundaries)
    target=out/'graph.bin';write(target,data)
    n,e,l,b,inputs=data['header']
    result=dict(scope='Fixed combinational LUT graph, uniform cell delay and explicit boundary snapshots; no routing, clocks, exceptions or hard-block timing',
        input_sha256=sha(args.input),graph_sha256=sha(target),source_sha256=sha(Path(__file__)),numpy_version=np.__version__,
        nodes=n,effective_edges=e,levels=l,snapshots=b,primary_inputs=inputs,primary_outputs=len(data['outputs']),
        delay_ps=args.delay_ps,seed=args.seed,binary_bytes=target.stat().st_size,
        graph_device_array_bytes=(8*n+2*4*(n+1)+2*4*e+4*n+(8 if e==0 else 0)),
        snapshot_device_array_bytes=4*8*n*b,
        quantization='Integer ps; explicit arrivals round up and required times round down to 1 ps',
        oracle='Independent Python topological forward/reverse gather, exact int64',
        maximum_truth_inputs=max((len(node['inputs']) for node in network['nodes']),default=0),
        cell_model_scope='User-selected uniform analytical delay; a LUT6 graph does not imply physical Gowin LUT6 support',
        outputs={p:int(i) for p,i in zip(network['outputs'],data['outputs'])})
    if args.constraints:result['constraint_sha256']=sha(args.constraints)
    (out/'manifest.json').write_text(json.dumps(result,indent=2)+'\n')
    (out/'names.json').write_text(json.dumps(data['names'])+'\n')
    print(json.dumps({k:v for k,v in result.items() if k!='outputs'},indent=2))


if __name__=='__main__':main()
