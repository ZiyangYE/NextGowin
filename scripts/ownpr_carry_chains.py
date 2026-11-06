"""Extract logical native ALU chains without replacing arithmetic resources."""
from collections import defaultdict


def extract(module):
    cells=module['cells'];alus={n:c for n,c in cells.items() if c['type']=='ALU'};drivers={};loads=defaultdict(list)
    for name,c in cells.items():
        for pin,bits in c['connections'].items():
            for bit in bits:
                if c['port_directions'][pin]=='output':
                    if bit in drivers:raise ValueError('Multiple drivers in carry contract')
                    drivers[bit]=(name,pin)
                else:loads[bit].append((name,pin))
    successor={};predecessor={};blockers=[]
    for name,c in alus.items():
        if set(c['connections'])!={'I0','I1','I3','CIN','COUT','SUM'} or any(len(v)!=1 for v in c['connections'].values()):raise ValueError('Unsupported ALU pin shape')
        mode=int(c['parameters']['ALU_MODE'],2)
        if mode not in range(9):raise ValueError('Uncalibrated ALU function mode')
        carry=c['connections']['COUT'][0];nexts=[n for n,p in loads[carry] if n in alus and p=='CIN']
        if len(nexts)>1:blockers.append(dict(cell=name,reason='Carry forks require explicit physical handling',destinations=nexts))
        elif nexts:
            successor[name]=nexts[0]
            if nexts[0] in predecessor:raise ValueError('Multiple ALU carry predecessors')
            predecessor[nexts[0]]=name
    chains=[];seen=set()
    for head in sorted(set(alus)-set(predecessor)):
        names=[];cursor=head
        while cursor is not None:
            if cursor in seen:raise ValueError('Cyclic or multiply owned carry chain')
            seen.add(cursor);names.append(cursor);cursor=successor.get(cursor)
        headbit=alus[head]['connections']['CIN'][0];tail=names[-1];tailbit=alus[tail]['connections']['COUT'][0]
        driver=drivers.get(headbit);constant=headbit if headbit in ('0','1') else None
        if driver and cells[driver[0]]['type'] in ('VCC','GND'):constant=str(int(cells[driver[0]]['type']=='VCC'))
        chains.append(dict(cells=names,length=len(names),modes=[int(alus[n]['parameters']['ALU_MODE'],2) for n in names],
            head_cin=headbit,head_constant=constant,head_driver=driver,tail_cout=tailbit,tail_loads=loads[tailbit],
            physical_placement_verified=False))
    if seen!=set(alus):raise ValueError('Carry cycle has no legal chain head')
    return dict(alu_cells=len(alus),chains=chains,chain_count=len(chains),max_length=max((c['length'] for c in chains),default=0),
        blockers=blockers,physical_carry_model_complete=False)
