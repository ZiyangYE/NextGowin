"""Build intrinsic-wire equivalence solely from native DLL query records."""
import argparse,json,time,hashlib
from pathlib import Path
import numpy as np
from extract_native_fse import Fse
from capture_runtime_tables import EXPECTED

def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--runtime',type=Path,required=True);ap.add_argument('--out',type=Path,required=True);a=ap.parse_args();a.out.mkdir(exist_ok=False)
    start=time.monotonic();m=json.loads((a.runtime/'manifest.json').read_text());f=Fse()
    if m['status']!='extracted' or not m['full_grid'] or m['open_chip_database_used'] or m['dll_sha256']!=EXPECTED:raise ValueError('Full native extraction required')
    if m['sources']['GW5AST-138C.fse']!=f.sha256:raise ValueError('Mixed device versions')
    p=a.runtime/'intrinsic_endpoints.bin'
    if sha(p)!=m['endpoints_sha256']:raise ValueError('Native endpoint records changed')
    n=len(m['nodes']);rows=len(f.grid);cols=len(f.grid[0]);count=rows*cols*n
    raw=np.fromfile(p,dtype='<i2').reshape(-1,6).astype(np.int32)
    for r,c,i in ((0,1,2),(3,4,5)):
        if np.any(raw[:,r]<1) or np.any(raw[:,r]>rows) or np.any(raw[:,c]<1) or np.any(raw[:,c]>cols) or np.any(raw[:,i]<0) or np.any(raw[:,i]>=n):raise ValueError('Intrinsic endpoints outside FSE grid')
    u=((raw[:,0]-1)*cols+(raw[:,1]-1))*n+raw[:,2];v=((raw[:,3]-1)*cols+(raw[:,4]-1))*n+raw[:,5];del raw
    parent=np.arange(count,dtype=np.uint32)
    converged=False
    for iteration in range(64):
        before=parent.copy()
        np.minimum.at(parent,u,parent[v]);np.minimum.at(parent,v,parent[u])
        while True:
            jumped=parent[parent]
            if np.array_equal(jumped,parent):break
            parent=jumped
        if np.array_equal(before,parent):converged=True;break
    if not converged:raise ValueError('Intrinsic equivalence did not converge')
    if np.any(parent[u]!=parent[v]):raise ValueError('Native physical wire aliases lost')
    components=np.bincount(parent,minlength=count);largest=int(components.max())
    # X1/X2/X8 extraction alone must not short a chip-scale routing network.
    if largest>64:raise ValueError(f'Suspicious intrinsic wire component size {largest}')
    order=np.argsort(parent,kind='stable').astype(np.uint32);sorted_roots=parent[order]
    np.save(a.out/'parent.npy',parent,allow_pickle=False);np.save(a.out/'members.npy',order,allow_pickle=False);np.save(a.out/'roots.npy',sorted_roots,allow_pickle=False)
    manifest=dict(status='built',source_runtime=str(a.runtime.resolve()),source_runtime_sha256=sha(a.runtime/'manifest.json'),source_fse_sha256=f.sha256,
        open_chip_database_used=False,rows=rows,columns=cols,node_types=n,local_nodes=count,intrinsic_alias_records=len(u),
        equivalence_components=int(np.count_nonzero(components)),largest_component_nodes=largest,iterations=iteration+1,wall_seconds=time.monotonic()-start,
        arrays_sha256={name:sha(a.out/name) for name in ('parent.npy','members.npy','roots.npy')},
        scope='Native X1/X2/X8 wire equivalence. Clock/long-wire gating, full site packing and timing signoff remain unvalidated.')
    (a.out/'manifest.json').write_text(json.dumps(manifest,indent=2));print(json.dumps(manifest,indent=2))
if __name__=='__main__':main()
