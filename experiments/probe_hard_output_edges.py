"""Read native fanouts around failed hard-output roots; no P&R or graph mutation."""
import argparse,json,os,subprocess,sys,time,shutil
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))


def main():
    p=argparse.ArgumentParser();p.add_argument('--out',type=Path,required=True);p.add_argument('--routes',type=Path);p.add_argument('--worker',action='store_true');a=p.parse_args();out=a.out.resolve()
    if a.worker:
        from native_device_session import NativeDeviceSession
        native=NativeDeviceSession(out);request=json.loads((out/'request.json').read_text());rows=[]
        for point in request['points']:
            point=tuple(point);children=native.fanouts(point)
            rows.append(dict(source=point,destinations=children,second_level=[dict(source=q,destinations=native.fanouts(q)) for q in children if min(q[:2])>=1]))
        (out/'fanouts.json').write_text(json.dumps(rows,indent=2));return
    from ownpr_device import Device,RoutingGraph,DLL_SHA
    from ownpr_image_storage import sha
    from workspace_budget import reserve
    from gowin_probe import prepare
    reserve(ROOT,20000000);prepare(out);graph=RoutingGraph(Device(),ROOT/'results/native_rrg_001')
    nets={n['name']:n for n in json.loads((a.routes/'nets.json').read_text())};progress=[json.loads(s) for s in (a.routes/'progress.jsonl').read_text().splitlines()]
    failures={r['net'] for r in progress if r['event']=='net_done' and r['iteration']==0 and r['failed']};points=[]
    for name in sorted(failures):
        n=nets[name]['source'];r,c=graph[n];points.append([r+1,c+1,n%graph.n])
    (out/'request.json').write_text(json.dumps(dict(points=sorted(set(map(tuple,points))),source_nets_sha256=sha(a.routes/'nets.json')),indent=2));shutil.copyfile(__file__,out/'source.py.txt')
    python=ROOT/'binary_audit/flow/runtime/bin/python.exe';start=time.monotonic()
    with (out/'worker.log').open('w') as log:r=subprocess.run([str(python),str(Path(__file__).resolve()),'--worker','--out',str(out)],cwd=python.parent,env=dict(os.environ,PYTHONHOME=sys.base_prefix),stdout=log,stderr=subprocess.STDOUT,timeout=60)
    result=dict(status='captured' if r.returncode==0 else 'failed',returncode=r.returncode,wall_seconds=time.monotonic()-start,dll_sha256=DLL_SHA,vendor_pnr_executed=False)
    if r.returncode==0:result['fanouts_sha256']=sha(out/'fanouts.json')
    (out/'manifest.json').write_text(json.dumps(result,indent=2));print(result)


if __name__=='__main__':main()
