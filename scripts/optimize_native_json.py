"""Checked native rewrites: wire aliases, local mux packing and FF fanout.

No register is retimed or removed. Replicas inherit every non-Q connection,
parameter and initial value; only consumer inputs are repartitioned.
"""
from collections import defaultdict
import copy
import itertools
import math


def mux_pins(cell):
    if cell['type']!='LUT3':return None
    truth=int(cell['parameters']['INIT'],2)
    for a,b,s in itertools.permutations(range(3)):
        expect=sum((((i>>b)&1) if ((i>>s)&1) else ((i>>a)&1))<<i for i in range(8))
        if truth==expect:return tuple(cell['connections']['I'+str(i)][0] for i in (a,b,s))
    return None


def optimize(design,limit=64):
    if not isinstance(limit,int) or limit<2:raise ValueError('Fanout limit must be >=2')
    design=copy.deepcopy(design);records=[]
    for name,module in design['modules'].items():
        if int(module.get('attributes',{}).get('blackbox','0'),2):continue
        cells=module.get('cells',{});aliases={};removed=[]
        def resolve(bit):
            seen=set()
            while bit in aliases:
                if bit in seen:raise ValueError('Cyclic identity aliases')
                seen.add(bit);bit=aliases[bit]
            return bit
        for cname,c in cells.items():
            if cname.startswith('ng_acd_') and c['type']=='LUT1' and int(c['parameters']['INIT'],2)==2 and not int(c.get('attributes',{}).get('keep','0'),2):
                source=c['connections']['I0'][0];target=c['connections']['F'][0]
                if target==source:raise ValueError('Self-driven identity LUT')
                aliases[target]=source;removed.append(cname)
        for bit in aliases:resolve(bit)
        for cname in removed:del cells[cname]
        for c in cells.values():
            c['connections']={p:[resolve(b) for b in bits] for p,bits in c['connections'].items()}
        for row in list(module.get('netnames',{}).values())+list(module.get('ports',{}).values()):row['bits']=[resolve(b) for b in row['bits']]
        keep={b for row in module.get('netnames',{}).values() if int(row.get('attributes',{}).get('keep','0'),2) for b in row['bits']}
        boundary={b for row in module.get('ports',{}).values() for b in row['bits']}
        def usage():
            drivers={};loads=defaultdict(list)
            for n,c in cells.items():
                for p,bits in c['connections'].items():
                    for i,b in enumerate(bits):
                        direction=c.get('port_directions',{}).get(p)
                        if direction=='output':drivers[b]=(n,p)
                        elif direction in ('input','inout'):loads[b].append((n,p,i))
            return drivers,loads
        drivers,loads=usage();packed=[];reserved=set()
        for cname,c in list(cells.items()):
            pins=mux_pins(c)
            if pins is None or cname in reserved or int(c.get('attributes',{}).get('keep','0'),2):continue
            a,b,s=pins
            if a==b or a in boundary or b in boundary or a in keep or b in keep:continue
            if len(loads[a])!=1 or len(loads[b])!=1:continue
            da=drivers.get(a);db=drivers.get(b)
            if not da or not db or da[0]==db[0] or da[0] in reserved or db[0] in reserved:continue
            ca=cells[da[0]];cb=cells[db[0]]
            if ca['type'] not in ('LUT1','LUT2','LUT3','LUT4') or cb['type'] not in ('LUT1','LUT2','LUT3','LUT4'):continue
            out=c['connections']['F']
            c.update(type='MUX2_LUT5',parameters={},connections=dict(I0=[a],I1=[b],S0=[s],O=out),
                     port_directions=dict(I0='input',I1='input',S0='input',O='output'))
            reserved.update((cname,da[0],db[0]));packed.append(dict(root=cname,leaves=[da[0],db[0]],truth_table_checked=True))
        drivers,loads=usage()
        maximum=max((b for c in cells.values() for bits in c['connections'].values() for b in bits if isinstance(b,int)),default=1)
        initial={}
        for row in module.get('netnames',{}).values():
            value=row.get('attributes',{}).get('init')
            if value is not None:
                for bit,val in zip(row['bits'],reversed(value)):
                    if bit in initial and initial[bit]!=val:raise ValueError('Conflicting aliased initial values')
                    initial[bit]=val
        copies=[]
        for cname,c in list(cells.items()):
            if c['type'] not in ('DFFRE','DFFSE','DFFCE','DFFPE'):continue
            if len(c['connections'].get('Q',[]))!=1:continue
            q=c['connections']['Q'][0];sinks=loads[q]
            if q in boundary or len(sinks)<=2*limit:continue
            # Dedicated clock/reset distribution is handled by native routing.
            if any(p.upper() in ('CLK','CLKA','CLKB','RESET','SET','CLEAR','PRESET') for _,p,_ in sinks):continue
            init=initial.get(q)
            param=c.get('parameters',{}).get('INIT')
            if init in ('x','z'):continue
            if param and set(param)-{'0','1'}:continue
            if init is None:init=param[-1] if param else ('1' if c['type'] in ('DFFSE','DFFPE') else '0')
            by_cell=defaultdict(list)
            for n,p,i in sinks:by_cell[n].append((p,i))
            groups=[];group=[];weight=0
            for n,pins in sorted(by_cell.items()):
                if group and weight+len(pins)>limit:groups.append(group);group=[];weight=0
                group.append((n,pins));weight+=len(pins)
            if group:groups.append(group)
            for index,group in enumerate(groups[1:],1):
                maximum+=1;newname=cname+'_ng_replica_'+str(index)
                if newname in cells:raise ValueError('Replica name collision')
                clone=copy.deepcopy(c);clone['connections']['Q']=[maximum]
                clone['attributes']={**clone.get('attributes',{}),'keep':'1'}
                if {p:b for p,b in clone['connections'].items() if p!='Q'}!={p:b for p,b in c['connections'].items() if p!='Q'} or clone['parameters']!=c['parameters']:raise AssertionError('Replica state controls changed')
                cells[newname]=clone
                # Native FF INIT is carried by its copied parameter/default.
                # A new RTL wire-init attribute would require dffinit again.
                module['netnames'][newname+'_Q']=dict(hide_name=0,bits=[maximum],attributes={'keep':'1'})
                for n,pins in group:
                    for p,i in pins:
                        if cells[n]['connections'][p][i]!=q:raise AssertionError('Consumer partition changed')
                        cells[n]['connections'][p][i]=maximum
            copies.append(dict(source=cname,q=q,initial=init,old_sink_pins=len(sinks),replicas=len(groups)-1,
                group_sink_pins=[sum(len(pins) for _,pins in g) for g in groups],
                proof='Identical state equation, clock/control pins, parameters and initial value; disjoint input-consumer partition.'))
        if removed or packed or copies:
            records.append(dict(module=name,removed_identity_luts=removed,wire_aliases={str(k):resolve(k) for k in aliases},
                mux_packs=packed,register_replication=copies))
    return design,dict(schema='native-netlist-optimizations-v1',fanout_limit=limit,modules=records,
        identity_luts_removed=sum(len(r['removed_identity_luts']) for r in records),
        mux5_packs=sum(len(r['mux_packs']) for r in records),registers_added=sum(x['replicas'] for r in records for x in r['register_replication']))
