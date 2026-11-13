"""Bounded native LUT replication; protected packing, exact boundary CEC.

This experiment partitions consumers by retained logical hierarchy. Its fanout
heuristic is not a physical timing guarantee or the complete FAPO algorithm.
State, dedicated mux ownership, clock and asynchronous-control nets are kept.
"""
from collections import Counter,defaultdict,deque
import copy
import math
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from register_control_map import logic
from gw5a_functional_map import blif
from run_abc_reproduction import cec


def replicate(design,limit=32,max_groups=8,area_allowance=.02):
    design=copy.deepcopy(design);reports=[]
    for mn,m in design['modules'].items():
        if int(m.get('attributes',{}).get('blackbox','0'),2):continue
        cells=m.get('cells',{});loads=defaultdict(list)
        for n,c in cells.items():
            for p,bs in c['connections'].items():
                if c.get('port_directions',{}).get(p) in ('input','inout'):
                    for i,b in enumerate(bs):loads[b].append((n,p,i))
        boundary={b for r in m.get('ports',{}).values() for b in r['bits']}
        base=sum(c['type'] in ('LUT1','LUT2','LUT3','LUT4') for c in cells.values());budget=math.floor(base*area_allowance)
        maximum=max((b for c in cells.values() for bs in c['connections'].values() for b in bs if isinstance(b,int)),default=1)
        maximum=max(maximum,max((b for r in m.get('netnames',{}).values() for b in r['bits'] if isinstance(b,int)),default=1))
        labels={}
        for n,r in m.get('netnames',{}).items():
            if n.startswith(('ng_','$')) or '.' not in n:continue
            label='.'.join(n.split('.')[:2])
            for b in r['bits']:
                if b not in labels or len(label)<len(labels[b]):labels[b]=label
        affinity_cache={}
        def affinity(n):
            if n in affinity_cache:return affinity_cache[n]
            found=[];queue=deque([(n,0)]);seen=set()
            while queue and len(seen)<24:
                name,depth=queue.popleft()
                if name in seen:continue
                seen.add(name);c=cells[name]
                outputs=[b for p,bs in c['connections'].items() if c.get('port_directions',{}).get(p)=='output' for b in bs]
                found.extend(labels[b] for b in outputs if b in labels)
                if depth<2 and not found:
                    for b in outputs:
                        for child,_,_ in loads[b]:queue.append((child,depth+1))
            result=Counter(found).most_common(1)[0][0] if found else n
            affinity_cache[n]=result;return result
        candidates=sorted((n for n,c in cells.items() if c['type'] in ('LUT1','LUT2','LUT3','LUT4')),
                          key=lambda n:-len(loads[cells[n]['connections']['F'][0]]))
        copies=[]
        for n in candidates:
            if not budget:break
            c=cells[n];q=c['connections']['F'][0];sinks=list(loads[q]);truth=int(c['parameters']['INIT'],2)
            if q in boundary or len(sinks)<=2*limit or int(c.get('attributes',{}).get('keep','0'),2):continue
            if truth in (0,(1<<(1<<int(c['type'][-1])))-1):continue
            unsafe=False
            for child,p,_ in sinks:
                kind=cells[child]['type'];pin=p.upper()
                if cells[child]['port_directions'][p]!='input' or pin in ('CLK','CLKA','CLKB','CLKIN','CLEAR','PRESET') or pin.startswith('CLK'):
                    unsafe=True;break
                if pin in ('RESET','SET') and kind not in ('DFFRE','DFFSE'):unsafe=True;break
                if kind in ('MUX2_LUT5','MUX2_LUT6') and pin in ('I0','I1'):unsafe=True;break
            if unsafe:continue
            count=min(math.ceil(len(sinks)/limit),max_groups,budget+1)
            grouped=defaultdict(list)
            for child,p,i in sinks:grouped[child].append((p,i))
            target=math.ceil(len(sinks)/count);groups=[[]];weight=0
            for child,pins in sorted(grouped.items(),key=lambda pair:(affinity(pair[0]),pair[0])):
                if groups[-1] and weight+len(pins)>target and len(groups)<count:groups.append([]);weight=0
                groups[-1].append((child,pins));weight+=len(pins)
            loads[q]=[(child,p,i) for child,pins in groups[0] for p,i in pins]
            for j,group in enumerate(groups[1:],1):
                name=n+'_ng_logic_replica_'+str(j)
                if name in cells:raise ValueError('Logic replica namespace collision')
                maximum+=1;clone=copy.deepcopy(c);clone['connections']['F']=[maximum];clone.setdefault('attributes',{}).update(keep='1',ng_generated='1')
                if {p:bs for p,bs in clone['connections'].items() if p!='F'}!={p:bs for p,bs in c['connections'].items() if p!='F'} or clone['parameters']!=c['parameters']:
                    raise AssertionError('Replica function changed')
                cells[name]=clone
                for p,bs in clone['connections'].items():
                    if clone['port_directions'][p]=='input':
                        for i,b in enumerate(bs):loads[b].append((name,p,i))
                for child,pins in group:
                    for p,i in pins:
                        if cells[child]['connections'][p][i]!=q:raise AssertionError('Sink partition was not disjoint')
                        cells[child]['connections'][p][i]=maximum;loads[maximum].append((child,p,i))
                copies.append(dict(source=n,clone=name,sinks=sum(len(pins) for _,pins in group),original_fanout=len(sinks)));budget-=1
        reports.append(dict(module=mn,original_luts=base,clones=len(copies),final_luts=base+len(copies),cap=base+math.floor(base*area_allowance),copies=copies))
    return design,dict(schema='native-lut-replication-v1',limit=limit,max_groups=max_groups,area_allowance=area_allowance,modules=reports,
                       clones=sum(r['clones'] for r in reports),scope='Exact cloned functions and partitioned sinks; heuristic hierarchy affinity. Native hold/setup signoff required.')


def prove(before,after,out):
    """Observe every actual non-LUT input pin; preserve hard-cell identity/state."""
    out.mkdir(parents=True);records=[]
    for mn,old in before['modules'].items():
        new=after['modules'][mn]
        if new==old:continue
        comb=lambda m:{n:c for n,c in m.get('cells',{}).items() if logic(c)}
        old_logic,new_logic=comb(old),comb(new)
        old_hard={n:c for n,c in old['cells'].items() if n not in old_logic}
        new_hard={n:c for n,c in new['cells'].items() if n not in new_logic}
        if set(old_hard)!=set(new_hard):raise ValueError('State/hard-resource set changed')
        observations=[]
        for n,c in old_hard.items():
            d=new_hard[n]
            if {k:v for k,v in c.items() if k!='connections'}!={k:v for k,v in d.items() if k!='connections'}:raise ValueError('Hard resource metadata changed')
            for p,bs in c['connections'].items():
                if c['port_directions'][p]=='input':
                    observations.extend((b,d['connections'][p][i]) for i,b in enumerate(bs))
                elif bs!=d['connections'][p]:raise ValueError('Hard output/inout changed')
        if old['ports']!=new['ports']:raise ValueError('Module interface changed')
        observations.extend((b,b) for r in old.get('ports',{}).values() if r['direction']!='input' for b in r['bits'])
        def network(m,gates,side):
            nodes=[];used=set();driven=set()
            def signal(b):return 'b'+str(b) if isinstance(b,int) else 'c'+b
            for c in gates.values():
                ins,o,t=logic(c);nodes.append(dict(inputs=[signal(b) for b in ins],output=signal(o),truth=t));used.update(signal(b) for b in ins);driven.add(signal(o))
            outputs=[]
            for i,pair in enumerate(observations):
                source=signal(pair[side]);name='observe_'+str(i);nodes.append(dict(inputs=[source],output=name,truth=2));used.add(source);driven.add(name);outputs.append(name)
            nodes.extend([dict(inputs=[],output='c0',truth=0),dict(inputs=[],output='c1',truth=1)])
            return dict(inputs=sorted(used-driven-{'c0','c1'}),outputs=outputs,nodes=nodes)
        folder=out/('module_'+str(len(records)));folder.mkdir();a,b=folder/'before.blif',folder/'after.blif'
        a.write_text(blif(network(old,old_logic,0)));b.write_text(blif(network(new,new_logic,1)))
        r=cec(ROOT/'build/abc/_TEST/abc.exe',a,b,folder/'cec.log',300)
        if not r['equivalent']:raise ValueError('Actual native replica boundary CEC failed')
        records.append(dict(module=mn,cec=r,hard_input_observations=len(observations)))
    return records
