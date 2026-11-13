"""Specialize constant add/sub control on complete, valid-seeded carry chains.

Mode 0 requires a zero initial carry and mode 1 a one initial carry. Mixed,
branched, externally seeded, or dynamically controlled chains are retained.
"""
from collections import defaultdict


def specialize(design):
    records=[]
    for name,module in design['modules'].items():
        if name.startswith('~') or int(module.get('attributes',{}).get('blackbox','0'),2):continue
        alus={n:c for n,c in module.get('cells',{}).items() if c['type']=='ALU'}
        drivers={c['connections']['COUT'][0]:n for n,c in alus.items() if c['connections'].get('COUT')}
        children=defaultdict(list)
        for n,c in alus.items():
            if c['connections']['CIN'][0] in drivers:children[drivers[c['connections']['CIN'][0]]].append(n)
        for root,c in alus.items():
            seed=c['connections']['CIN']
            if seed[0] in drivers:continue
            chain=[];current=root
            while current is not None:
                if current in chain:raise ValueError('Cyclic carry chain')
                chain.append(current);next_nodes=children[current]
                if len(next_nodes)>1:break
                current=next_nodes[0] if next_nodes else None
            if any(len(children[n])>1 for n in chain):continue
            if any(int(alus[n]['parameters']['ALU_MODE'],2)!=2 for n in chain):continue
            controls={tuple(alus[n]['connections']['I3']) for n in chain}
            if controls=={('1',)} and seed==['0']:mode=0
            elif controls=={('0',)} and seed==['1']:mode=1
            else:continue
            for n in chain:alus[n]['parameters']['ALU_MODE']=format(mode,'032b')
            records.append(dict(module=name,root=root,cells=chain,seed=seed,constant_control=list(next(iter(controls))),old_mode=2,new_mode=mode))
    return dict(schema='gw5a-alu-mode-specialization-v1',scope='Only complete homogeneous mode-2 carry chains with legal constant initial carry; all pins and state unchanged.',
                alu_cells_specialized=sum(len(r['cells']) for r in records),chains=records)
