"""Inventory native carry chains excluded by the carry-only lowering rule."""
import argparse
from collections import Counter,defaultdict
import json
from pathlib import Path

ap=argparse.ArgumentParser();ap.add_argument('source',type=Path);ap.add_argument('--out',type=Path,required=True);args=ap.parse_args()
d=json.loads(args.source.read_text());rows=[];hist=Counter()
for mn,m in d['modules'].items():
    if mn.startswith('~') or int(m.get('attributes',{}).get('blackbox','0'),2):continue
    cells=m.get('cells',{});alus={n:c for n,c in cells.items() if c['type']=='ALU'}
    loads=Counter(b for c in cells.values() for p,bs in c['connections'].items() if c['port_directions'].get(p) in ('input','inout') for b in bs)
    loads.update(b for r in m.get('ports',{}).values() if r['direction']!='input' for b in r['bits'])
    for r in m.get('netnames',{}).values():
        if int(r.get('attributes',{}).get('keep','0'),2):loads.update(r['bits'])
    drivers={c['connections']['COUT'][0]:n for n,c in alus.items()};children=defaultdict(list)
    for n,c in alus.items():
        parent=drivers.get(c['connections']['CIN'][0])
        if parent:children[parent].append(n)
    for root,c in alus.items():
        if c['connections']['CIN'][0] in drivers:continue
        chain=[];stack=[root]
        while stack:
            n=stack.pop()
            if n in chain:raise ValueError('Cyclic carry chain')
            chain.append(n);stack.extend(children[n])
        modes=sorted({int(alus[n]['parameters']['ALU_MODE'],2) for n in chain})
        sums=[n for n in chain if any(loads[b] for b in alus[n]['connections'].get('SUM',[]))]
        hist[(len(chain),len(sums),tuple(modes))]+=1
        if 0<len(sums)<=2 and len(chain)>=8 and set(modes)<=set(range(3)):
            rows.append(dict(module=mn,root=root,length=len(chain),used_sums=sums,modes=modes))
r=dict(source=str(args.source),sparse_output_chains=rows,total_eligible_alu_cells=sum(x['length'] for x in rows),
       histogram=[dict(length=a,used_sums=b,modes=c,count=n) for (a,b,c),n in sorted(hist.items())])
args.out.write_text(json.dumps(r,indent=2)+'\n')
print('Sparse-output candidates:',len(rows),'ALU cells:',r['total_eligible_alu_cells'])
print(Counter((x['length'],len(x['used_sums'])) for x in rows))
