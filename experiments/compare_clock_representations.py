"""Compare physical clock timing for two representations, without P&R."""
import argparse,json,os,shutil,subprocess,sys,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from ownpr_image_storage import sha
from workspace_budget import reserve


def worker(out):
    from native_device_session import NativeDeviceSession
    from ownpr_clock_graph import ClockGraph
    from ownpr_clock_timing_search import measure
    native=NativeDeviceSession(out);request=json.loads((out/'request.json').read_text());graph=ClockGraph(Path(request['inventory']));results=[]
    for label in ('left','right'):
        tree=json.loads((out/(label+'.json')).read_text())
        if tree['topology_sha256']!=graph.sha256 or graph.audit_paths(tree['paths_sink_to_source'])['status']!='clock_paths_match':raise ValueError('Unverified clock paths')
        parents={tuple(x['node']):tuple(x['parent']) if x['parent'] else None for x in tree['parents']};resolved=native.resolve_routed_nodes(parents)
        pointers={graph.by_coordinate[k]:p for k,p in resolved.items()};paths=[[graph.by_coordinate[tuple(p)] for p in reversed(path)] for path in tree['paths_sink_to_source']]
        rows,values=measure(native,graph,paths,pointers);result=dict(sinks=rows,nodes=[dict(node=list(n),values=v) for n,v in values.items()])
        (out/(label+'_timing.json')).write_text(json.dumps(result,indent=2));results.append({tuple(r['node']):r['delays_by_selector'] for r in rows})
    left,right=results
    if set(left)!=set(right):raise ValueError('Clock endpoint sets changed')
    changes=[dict(node=list(n),maximum_absolute_difference_ns=max(abs(a-b) for av,bv in zip(left[n],right[n]) for a,b in zip(av,bv))) for n in sorted(left) if left[n]!=right[n]]
    (out/'comparison.json').write_text(json.dumps(dict(status='clock_representations_measured',sinks=len(left),changed_sinks=len(changes),changes=changes,
        maximum_absolute_difference_ns=max((r['maximum_absolute_difference_ns'] for r in changes),default=0.),vendor_pnr_executed=False,full_sta_verified=False),indent=2))


def main():
    p=argparse.ArgumentParser();p.add_argument('--left',type=Path);p.add_argument('--right',type=Path);p.add_argument('--inventory',type=Path);p.add_argument('--out',type=Path,required=True);p.add_argument('--worker',action='store_true');a=p.parse_args();out=a.out.resolve()
    if a.worker:return worker(out)
    if not all((a.left,a.right,a.inventory)):p.error('Both trees and inventory required')
    from gowin_probe import prepare
    from ownpr_device import DLL_SHA
    reserve(ROOT,30_000_000);prepare(out)
    request=dict(inventory=str(a.inventory.resolve()),source_sha256={str(f.resolve()):sha(f) for f in (a.left,a.right)})
    (out/'request.json').write_text(json.dumps(request,indent=2));shutil.copyfile(a.left,out/'left.json');shutil.copyfile(a.right,out/'right.json');shutil.copyfile(__file__,out/'source.py.txt')
    python=ROOT/'binary_audit/flow/runtime/bin/python.exe';start=time.monotonic()
    with (out/'worker.log').open('w') as log:cp=subprocess.run([str(python),str(Path(__file__).resolve()),'--worker','--out',str(out)],cwd=python.parent,env=dict(os.environ,PYTHONHOME=sys.base_prefix),stdout=log,stderr=subprocess.STDOUT,timeout=120)
    r=dict(status='captured' if cp.returncode==0 else 'failed',returncode=cp.returncode,wall_seconds=time.monotonic()-start,dll_sha256=DLL_SHA,vendor_pnr_executed=False,request=request)
    if cp.returncode==0:r['comparison']=json.loads((out/'comparison.json').read_text());r['output_sha256']={n:sha(out/n) for n in ('left_timing.json','right_timing.json','comparison.json')}
    (out/'manifest.json').write_text(json.dumps(r,indent=2));print(json.dumps({k:v for k,v in r.items() if k!='comparison'},indent=2))
    if cp.returncode:raise SystemExit(1)


if __name__=='__main__':main()
