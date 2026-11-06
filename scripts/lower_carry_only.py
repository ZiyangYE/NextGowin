"""Experimental Boolean lowering of entire carry chains with no used SUM.

This is deliberately separate from the default flow pending physical results.
Modes 0/1/2 use the actual native equation COUT = S ? CIN : I0.
"""
from collections import Counter,defaultdict
import copy


def lower(design):
    design=copy.deepcopy(design);records=[]
    for mn,m in design['modules'].items():
        if mn.startswith('~') or int(m.get('attributes',{}).get('blackbox','0'),2):continue
        cells=m.get('cells',{});alus={n:c for n,c in cells.items() if c['type']=='ALU'}
        loads=Counter(b for c in cells.values() for p,bs in c['connections'].items()
                      if c.get('port_directions',{}).get(p) in ('input','inout') for b in bs)
        loads.update(b for r in m.get('ports',{}).values() if r['direction']!='input' for b in r['bits'])
        for r in m.get('netnames',{}).values():
            if int(r.get('attributes',{}).get('keep','0'),2):loads.update(r['bits'])
        drivers={c['connections']['COUT'][0]:n for n,c in alus.items()}
        child=defaultdict(list)
        for n,c in alus.items():
            parent=drivers.get(c['connections']['CIN'][0])
            if parent:child[parent].append(n)
        maximum=max((b for c in cells.values() for bs in c['connections'].values() for b in bs if isinstance(b,int)),default=1)
        maximum=max(maximum,max((b for r in list(m.get('netnames',{}).values())+list(m.get('ports',{}).values()) for b in r['bits'] if isinstance(b,int)),default=1))
        def gate(name,kind,inputs,output=None):
            nonlocal maximum
            if name in cells:raise ValueError('Carry lowering name collision')
            if output is None:maximum+=1;output=maximum
            cells[name]=dict(type=kind,parameters={},attributes={},connections={**{p:[b] for p,b in inputs.items()},'Y':[output]},
                            port_directions={**{p:'input' for p in inputs},'Y':'output'})
            return output
        for root,c in alus.items():
            if c['connections']['CIN'][0] in drivers:continue
            chain=[];stack=[root]
            while stack:
                n=stack.pop()
                if n in chain:raise ValueError('Cyclic carry chain')
                chain.append(n);stack.extend(child[n])
            if any(loads[b] for n in chain for b in alus[n]['connections'].get('SUM',[])):continue
            if any(int(alus[n]['parameters']['ALU_MODE'],2) not in (0,1,2) or
                   int(alus[n].get('attributes',{}).get('keep','0'),2) for n in chain):continue
            for n in chain:
                a=alus[n];pins=a['connections'];mode=int(a['parameters']['ALU_MODE'],2)
                x=gate(n+'_ng_xor','$_XOR_',dict(A=pins['I0'][0],B=pins['I1'][0]))
                ctrl='1' if mode==0 else '0' if mode==1 else pins['I3'][0]
                if ctrl=='0':x=gate(n+'_ng_inv','$_NOT_',dict(A=x))
                elif ctrl!='1':
                    inv=gate(n+'_ng_ctrl','$_NOT_',dict(A=ctrl))
                    x=gate(n+'_ng_sel','$_XOR_',dict(A=x,B=inv))
                gate(n+'_ng_carry','$_MUX_',dict(A=pins['I0'][0],B=pins['CIN'][0],S=x),pins['COUT'][0])
                del cells[n]
            records.append(dict(module=mn,root=root,cells=chain))
    return design,dict(schema='carry-only-boolean-v1',chains=records,alu_cells_removed=sum(len(r['cells']) for r in records),
                       scope='Whole mode-0/1/2 chains with no consumed, exported or kept SUM; no state changes. Experimental, not default.')
