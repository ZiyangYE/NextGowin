"""Expose short GW5A ALU chains to Boolean mapping; retain long carry chains."""
import copy
from collections import defaultdict
from dsp_output_register_pack import kept

def lower(source,max_length=12):
    if not 1<=max_length<=16:raise ValueError('Short-chain bound must be 1..16')
    d=copy.deepcopy(source);records=[]
    for mn,m in d['modules'].items():
        if mn.startswith('~') or int(m.get('attributes',{}).get('blackbox','0'),2):continue
        cells=m['cells'];alus={n:c for n,c in cells.items() if c['type']=='ALU'}
        drivers={c['connections']['COUT'][0]:n for n,c in alus.items() if isinstance(c['connections']['COUT'][0],int)};children=defaultdict(list)
        for n,c in alus.items():
            p=drivers.get(c['connections']['CIN'][0])
            if p:children[p].append(n)
        maximum=max((b for r in list(m.get('netnames',{}).values())+list(m.get('ports',{}).values()) for b in r['bits'] if isinstance(b,int)),default=1)
        maximum=max(maximum,max((b for c in cells.values() for bs in c['connections'].values() for b in bs if isinstance(b,int)),default=1))
        def gate(name,kind,pins,output=None):
            nonlocal maximum
            if name in cells:raise ValueError('Short-carry namespace collision')
            if output is None:maximum+=1;output=maximum
            if not isinstance(output,int):return output
            cells[name]=dict(type=kind,hide_name=0,parameters={},attributes={},connections={**{p:[b] for p,b in pins.items()},'Y':[output]},port_directions={**{p:'input' for p in pins},'Y':'output'});return output
        for root,c in alus.items():
            if c['connections']['CIN'][0] in drivers:continue
            chain=[];todo=[root]
            while todo:
                n=todo.pop()
                if n in chain:raise ValueError('Cyclic carry graph')
                chain.append(n);todo.extend(children[n])
            if len(chain)>max_length or any(kept(alus[n].get('attributes',{})) or int(alus[n]['parameters']['ALU_MODE'],2) not in range(9) for n in chain):continue
            for n in chain:
                c=alus[n];p=c['connections'];mode=int(c['parameters']['ALU_MODE'],2);a,b,s,ci=[p[k][0] for k in ('I0','I1','I3','CIN')]
                if mode==0:prop=gate(n+'_ng_short_p','$_XOR_',dict(A=a,B=b))
                elif mode in (1,3,4,5):prop=gate(n+'_ng_short_p','$_XNOR_',dict(A=a,B=b))
                elif mode==2:
                    x=gate(n+'_ng_short_x','$_XOR_',dict(A=a,B=b));prop=gate(n+'_ng_short_p','$_XNOR_',dict(A=x,B=s))
                elif mode==6:prop=a
                elif mode==7:prop=gate(n+'_ng_short_p','$_NOT_',dict(A=a))
                else:prop=gate(n+'_ng_short_p','$_XNOR_',dict(A=a,B=s))
                gen='1' if mode in (3,7) else '0' if mode==6 else b if mode==5 else a
                gate(n+'_ng_short_sum','$_XOR_',dict(A=prop,B=ci),p['SUM'][0]);gate(n+'_ng_short_carry','$_MUX_',dict(A=gen,B=ci,S=prop),p['COUT'][0]);del cells[n]
            records.append(dict(module=mn,root=root,cells=chain))
    return d,dict(schema='short-carry-boolean-v1',max_length=max_length,chains=records,alu_cells_removed=sum(len(r['cells']) for r in records),scope='Exact GW5A modes 0..8 Boolean equations. Whole short chains only, no state changes; native physical acceptance required.')
