"""Replicate complete legal GW5A LUT/MUX clusters, under a LUT-slot cap.

This extends fanout repair to dedicated MUX5/MUX6 outputs. Private data leaves
stay exclusive. Logical hierarchy is a heuristic; routed timing decides QoR.
"""
from collections import Counter,defaultdict,deque
import copy
import math
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from dsp_output_register_pack import kept
from gw5a_functional_map import validate_packing
from run_abc_reproduction import sha


def replicate(source,limit=32,max_groups=4,area_allowance=.01):
    if limit<1 or max_groups<2 or not 0<=area_allowance<=.02:raise ValueError('Invalid cluster bounds')
    validate_packing(source);design=copy.deepcopy(source);reports=[]
    for mn,m in design['modules'].items():
        if mn.startswith('~') or int(m.get('attributes',{}).get('blackbox','0'),2):continue
        cells=m.get('cells',{});loads=defaultdict(list);drivers={}
        for n,c in cells.items():
            for p,bs in c['connections'].items():
                for i,b in enumerate(bs):
                    direction=c['port_directions'].get(p)
                    if direction=='output':drivers[b]=n
                    elif direction in ('input','inout'):loads[b].append((n,p,i))
        protected={b for r in m.get('ports',{}).values() for b in r['bits']}
        protected.update(b for r in m.get('netnames',{}).values() if kept(r.get('attributes',{})) for b in r['bits'])
        base=sum(c['type'] in ('LUT1','LUT2','LUT3','LUT4') for c in cells.values());budget=math.floor(base*area_allowance)
        maximum=max((b for c in cells.values() for bs in c['connections'].values() for b in bs if isinstance(b,int)),default=1)
        maximum=max(maximum,max((b for r in list(m.get('ports',{}).values())+list(m.get('netnames',{}).values()) for b in r['bits'] if isinstance(b,int)),default=1))
        labels={}
        for name,r in m.get('netnames',{}).items():
            if name.startswith(('ng_','$')) or '.' not in name:continue
            label='.'.join(name.split('.')[:2])
            for b in r['bits']:labels.setdefault(b,label)
        affinity_cache={}
        def affinity(name):
            if name in affinity_cache:return affinity_cache[name]
            found=[];queue=deque([(name,0)]);seen=set()
            while queue and len(seen)<24:
                child,depth=queue.popleft()
                if child in seen:continue
                seen.add(child);c=cells[child]
                outs=[b for p,bs in c['connections'].items() if c['port_directions'].get(p)=='output' for b in bs]
                found.extend(labels[b] for b in outs if b in labels)
                if depth<2 and not found:
                    for b in outs:
                        queue.extend((n,depth+1) for n,_,_ in loads[b])
            result=Counter(found).most_common(1)[0][0] if found else name
            affinity_cache[name]=result;return result
        def cluster(name):
            result=[];seen=set()
            def visit(n,parent=None):
                if n in seen:raise ValueError('Shared cluster descendant')
                seen.add(n);c=cells[n];kind=c['type'];out=c['connections']['O' if kind.startswith('MUX') else 'F'][0]
                attrs=dict(c.get('attributes',{}))
                # The checked mapper marks private leaves keep so that generic
                # cleanup cannot merge them. A complete copy retains both the
                # original and that ownership marker. User preservation flags
                # and kept net observations remain protected.
                if n.startswith(('ng_acd_','ng_sync_')) and int(attrs.get('ng_generated','0'),2):attrs.pop('keep',None)
                if kept(attrs) or out in protected:raise ValueError('Protected cluster')
                if parent is not None and (len(loads[out])!=1 or loads[out][0][0]!=parent):raise ValueError('Non-private cluster')
                if kind in ('MUX2_LUT5','MUX2_LUT6'):
                    allowed=('MUX2_LUT5',) if kind=='MUX2_LUT6' else ('LUT1','LUT2','LUT3','LUT4')
                    for pin in ('I0','I1'):
                        b=c['connections'][pin][0];child=drivers.get(b)
                        if child is None or cells[child]['type'] not in allowed:raise ValueError('Wrong dedicated child')
                        visit(child,n)
                elif kind not in ('LUT1','LUT2','LUT3','LUT4'):raise ValueError('Wrong cluster leaf')
                result.append(n)
            visit(name);return result
        roots=sorted((n for n,c in cells.items() if c['type'] in ('MUX2_LUT5','MUX2_LUT6')),
                     key=lambda n:(-len(loads[cells[n]['connections']['O'][0]]),n))
        copies=[];added_slots=0
        for root in roots:
            q=cells[root]['connections']['O'][0];sinks=list(loads[q])
            if len(sinks)<=2*limit:continue
            if any(cells[n]['port_directions'][p]!='input' or p.upper().startswith(('CLK','CLOCK')) or p.upper() in ('CLEAR','PRESET') or
                   (p.upper() in ('RESET','SET') and cells[n]['type'] not in ('DFFRE','DFFSE')) or
                   (cells[n]['type'].startswith('MUX2_LUT') and p in ('I0','I1')) for n,p,_ in sinks):continue
            try:members=cluster(root)
            except ValueError:continue
            slots=sum(cells[n]['type'].startswith('LUT') for n in members)
            count=min(math.ceil(len(sinks)/limit),max_groups,1+budget//slots)
            if count<2:continue
            ordered=sorted(sinks,key=lambda r:(affinity(r[0]),r[0],r[1],r[2]))
            target=math.ceil(len(ordered)/count);groups=[ordered[i:i+target] for i in range(0,len(ordered),target)]
            loads[q]=groups[0]
            for j,group in enumerate(groups[1:],1):
                aliases={};names={n:n+'_ng_cluster_replica_'+str(j) for n in members}
                if any(n in cells for n in names.values()):raise ValueError('Cluster namespace collision')
                for n in members:
                    c=cells[n];pin='O' if c['type'].startswith('MUX') else 'F';maximum+=1;aliases[c['connections'][pin][0]]=maximum
                for n in members:
                    clone=copy.deepcopy(cells[n]);clone.setdefault('attributes',{}).update(keep='1',ng_generated='1')
                    for p,bs in clone['connections'].items():clone['connections'][p]=[aliases.get(b,b) for b in bs]
                    cells[names[n]]=clone
                    for p,bs in clone['connections'].items():
                        for i,b in enumerate(bs):
                            if clone['port_directions'][p]=='input':loads[b].append((names[n],p,i))
                            elif clone['port_directions'][p]=='output':drivers[b]=names[n]
                for n,p,i in group:
                    if cells[n]['connections'][p][i]!=q:raise ValueError('Stale cluster sink')
                    cells[n]['connections'][p][i]=aliases[q]
                loads[aliases[q]]=group
                copies.append(dict(source=root,clone=names[root],members=names,leaf_slots=slots,original_fanout=len(sinks),sinks=len(group)))
                budget-=slots;added_slots+=slots
        reports.append(dict(module=mn,original_lut_slots=base,added_lut_slots=added_slots,copies=copies))
    packing=validate_packing(design)
    return design,dict(schema='gw5a-private-cluster-replication-v1',implementation_sha256=sha(Path(__file__)),
        limit=limit,max_groups=max_groups,area_allowance=area_allowance,modules=reports,
        clusters=sum(len(r['copies']) for r in reports),added_lut_slots=sum(r['added_lut_slots'] for r in reports),packing=packing,scope=__doc__)
