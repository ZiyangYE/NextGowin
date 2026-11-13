"""Bounded native-RRG path existence diagnostic, not complete routability/STA."""
import argparse,json,time
from pathlib import Path
from collections import defaultdict
from ownpr_device import Device,RoutingGraph,ROOT
from ownpr_router import connection_search
from ownpr_ioreg_macros import reserved_points
from ownpr_image_storage import load_json,sha


def main():
    p=argparse.ArgumentParser();p.add_argument('--checkpoint',type=Path,required=True);p.add_argument('--placement',type=Path,required=True);p.add_argument('--out',type=Path,required=True);a=p.parse_args()
    graph=RoutingGraph(Device(),ROOT/'results/native_rrg_001');source=a.checkpoint
    clock=load_json(source/'clock_tree.json');blocked={graph.node(r-1,c-1,graph.device.names[i]) for e in clock['parents'] for r,c,i in [e['node']] if min(r,c)>=1 and i in graph.device.names}
    blocked.update(graph.node(r-1,c-1,graph.device.names[i]) for r,c,i in reserved_points(load_json(a.placement/'packed.json'),load_json(a.placement/'placement.json')))
    checkpoint=load_json(source/'partial_routes.json');nets={n['name']:n for n in load_json(source/'nets.json')};last={}
    for line in (source/'progress.jsonl').read_text().splitlines():
        row=json.loads(line)
        if row['event']=='net_done':last[row['net']]=row.get('failed',[])
    started=time.monotonic();rows=[]
    report={'scope':'Geometric directed path existence with actual clock/IOREG reservations; other nets NOT reserved; full-chip search without ROI. No loaded timing or complete routing claim.',
            'source_sha256':{str(source/f):sha(source/f) for f in ('partial_routes.json','clock_tree.json','progress.jsonl','nets.json')},'cases':rows}
    def edges(node):return tuple((v,.05,pip) for v,cost,pip in graph.uphill(node))
    def deadline():
        if time.monotonic()-started>90:raise TimeoutError('Bounded reachability diagnostic')
    for name,failures in sorted(last.items()):
        for old in failures:
            root=nets[name]['source'];r=checkpoint['nets'].get(name,{'nodes':[root],'estimated_arrival_cost':{str(root):0.}})
            nodes=set(r['nodes']);dist={int(n):v for n,v in r['estimated_arrival_cost'].items()};t=time.monotonic();sink=old['sink']
            try:
                path,expanded,limited=connection_search(graph,sink,nodes,dist,.5,defaultdict(set),defaultdict(float),0.,blocked,{},name,.03,1.,100000,deadline,edges=edges,reverse=True)
                if path:
                    assert path[0][0] in nodes and path[-1][1]==sink
                    for u,v,pip,delay in path:assert v not in blocked and any(x==v and tuple(p)==tuple(pip) for x,cost,p in graph.downhill(u))
                row={'net':name,'sink':sink,'found':path is not None,'expanded':expanded,'limited':limited,'seconds':time.monotonic()-t,
                     'partial_tree_present':name in checkpoint['nets'],'pips':[list(p) for u,v,p,c in path] if path else []}
            except TimeoutError as error:row={'net':name,'sink':sink,'error':str(error)}
            rows.append(row);report['seconds']=time.monotonic()-started;a.out.write_text(json.dumps(report,indent=2)+'\n')
            print({k:v for k,v in row.items() if k!='pips'},flush=True)
            if 'error' in row:return


if __name__=='__main__':main()
