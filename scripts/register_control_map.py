"""Proved co-mapping of private LUT/mux cones into synchronous FF controls.

No FF is added, removed, retimed or assigned a new clock. Each rewrite checks
the actual old/new next-state truth tables for every boundary/state assignment.
Analytical setup costs rank proposals; native P&R remains the acceptance test.
"""
from collections import defaultdict,deque
import copy
import itertools
import math
from gw5a_functional_map import reduce_function,cofactor


def logic(c):
    kind=c['type'];p=c['connections']
    if kind in ('LUT1','LUT2','LUT3','LUT4'):
        return [p['I'+str(i)][0] for i in range(int(kind[-1]))],p['F'][0],int(c['parameters']['INIT'],2)
    if kind in ('MUX2_LUT5','MUX2_LUT6'):
        return [p[k][0] for k in ('I0','I1','S0')],p['O'][0],0xCA
    return None


def normalize(inputs,truth):
    unique=tuple(dict.fromkeys(b for b in inputs if b not in ('0','1')))
    tt=0
    for a in range(1<<len(unique)):
        values=dict(zip(unique,[(a>>i)&1 for i in range(len(unique))]));values.update({'0':0,'1':1})
        index=sum(values[b]<<i for i,b in enumerate(inputs));tt|=((truth>>index)&1)<<a
    return reduce_function(unique,tt)


def fold(design,max_changes=2048,area_allowance=0.02,min_gain_ps=1.0):
    design=copy.deepcopy(design);records=[]
    for mn,m in design['modules'].items():
        if mn.startswith('~') or int(m.get('attributes',{}).get('blackbox','0'),2):continue
        cells=m.get('cells',{});netnames=m.get('netnames',{})
        protected={b for r in netnames.values() if int(r.get('attributes',{}).get('keep','0'),2) for b in r['bits']}
        protected.update(b for r in m.get('ports',{}).values() for b in r['bits'])
        constants={}
        for c in cells.values():
            if c['type'] in ('VCC','GND'):
                p='V' if c['type']=='VCC' else 'G'
                for b in c['connections'][p]:constants[b]='1' if c['type']=='VCC' else '0'
        def const(b):return constants.get(b,b)
        initial=sum(c['type'] in ('LUT1','LUT2','LUT3','LUT4') for c in cells.values());cap=initial+math.floor(initial*area_allowance)
        current_count=initial
        maximum=max((b for c in cells.values() for bs in c['connections'].values() for b in bs if isinstance(b,int)),default=1)
        number=0
        def usage():
            drivers={};loads=defaultdict(list)
            for n,c in cells.items():
                for p,bs in c['connections'].items():
                    for b in bs:
                        if c.get('port_directions',{}).get(p)=='output':drivers[b]=n
                        elif c.get('port_directions',{}).get(p)=='input':loads[b].append((n,p))
            return drivers,loads
        drivers,loads=usage();arrivals={'0':0.0,'1':0.0}
        for p in m.get('ports',{}).values():
            if p['direction']=='input':arrivals.update({b:0.0 for b in p['bits']})
        pending={n:c for n,c in cells.items() if logic(c)}
        for c in cells.values():
            if not logic(c):
                for p,bs in c['connections'].items():
                    if c.get('port_directions',{}).get(p)=='output':arrivals.update({b:0.0 if c['type'] in ('VCC','GND') else 344.0 for b in bs})
        while pending:
            ready=[]
            for n,c in pending.items():
                ins,out,tt=logic(c)
                if all(b in arrivals for b in ins):
                    if c['type'].startswith('MUX'):
                        arrivals[out]=max(arrivals[ins[0]]+200,arrivals[ins[1]]+200,arrivals[ins[2]]+521)
                    else:arrivals[out]=max((arrivals[b] for b in ins),default=0)+521
                    ready.append(n)
            if not ready:raise ValueError('Cycle or unknown boundary in register mapper')
            for n in ready:del pending[n]
        state_cells=[(n,c) for n,c in cells.items() if c['type'] in ('DFFRE','DFFSE','DFFCE','DFFPE')]
        state_cells.sort(key=lambda item:-arrivals.get(item[1]['connections']['D'][0],0))
        for ffname,ff in state_cells:
            if len(records)>=max_changes:break
            if int(ff.get('attributes',{}).get('keep','0'),2):continue
            p=ff['connections'];d=p['D'][0];root=drivers.get(d)
            if root not in cells or not logic(cells[root]) or loads[d]!=[(ffname,'D')] or d in protected:continue
            if int(cells[root].get('attributes',{}).get('keep','0'),2):continue
            old_ctrl={'DFFRE':'RESET','DFFSE':'SET','DFFCE':'CLEAR','DFFPE':'PRESET'}[ff['type']]
            old_active=int(ff['type'] in ('DFFSE','DFFPE'))
            ce=const(p['CE'][0]);ctrl=const(p[old_ctrl][0]);q=p['Q'][0]
            if old_ctrl in ('CLEAR','PRESET') and ctrl!='0':continue
            cone={root};front=list(dict.fromkeys(const(b) for b in logic(cells[root])[0]))
            # Grow a private window, retaining shared/global logic as boundaries.
            for _ in range(12):
                changed=False
                for b in list(front):
                    source=drivers.get(b)
                    if source in cone or source not in cells or not logic(cells[source]) or len(loads[b])!=1 or b in protected:continue
                    c=cells[source]
                    if int(c.get('attributes',{}).get('keep','0'),2) and not source.startswith('ng_acd_'):continue
                    proposal=list(dict.fromkeys(x for old in front for x in ([const(x) for x in logic(c)[0]] if old==b else [old])))
                    if len([x for x in proposal if x not in ('0','1')])>6:continue
                    cone.add(source);front=proposal;changed=True;break
                if not changed:break
            boundary=tuple(b for b in front if b not in ('0','1'))
            if not 1<=len(boundary)<=6:continue
            tt=0
            for a in range(1<<len(boundary)):
                values={b:(a>>i)&1 for i,b in enumerate(boundary)};values.update({'0':0,'1':1})
                def evaluate(b):
                    b=const(b)
                    if b in values:return values[b]
                    c=cells[drivers[b]];ins,_,truth=logic(c);value=truth>>sum(evaluate(x)<<i for i,x in enumerate(ins))&1;values[b]=value;return value
                tt|=evaluate(d)<<a
            boundary,tt=reduce_function(boundary,tt)
            if not boundary:continue
            old_delay=max(arrivals[d]+57,arrivals.get(p['CE'][0],0)+335,arrivals.get(p[old_ctrl][0],0)+335)
            removed=sum(cells[n]['type'].startswith('LUT') for n in cone)
            best=None
            for select,b in enumerate(boundary):
                rest=boundary[:select]+boundary[select+1:]
                for value in (0,1):
                    fixed=cofactor(tt,len(boundary),select,value)
                    if fixed not in (0,(1<<(1<<len(rest)))-1):continue
                    active=int(bool(fixed))
                    if ctrl!='0' and active!=old_active:continue
                    ds,dt=reduce_function(rest,cofactor(tt,len(boundary),select,1-value))
                    if len(ds)>4:continue
                    cs0=[ctrl,ce,b];ct0=sum((int((i&1) or ((i>>1&1) and (i>>2&1)==value)))<<i for i in range(8))
                    cs,ct=normalize(cs0,ct0)
                    def estimate(ins,truth):
                        if not ins:return 0.0,0,None
                        if len(ins)==1 and truth==2:return arrivals.get(ins[0],0),0,None
                        inverse=None
                        if len(ins)==1 and truth==1:
                            producer=drivers.get(ins[0]);c=cells.get(producer,{})
                            if c.get('type') in ('LUT1','LUT2','LUT3','LUT4'):
                                inverse=producer;return arrivals.get(ins[0],0),1,inverse
                        return max(arrivals.get(x,0) for x in ins)+521,1,None
                    da,darea,dinv=estimate(ds,dt);ca,carea,cinv=estimate(cs,ct)
                    count=current_count-removed+darea+carea
                    delay=max(da+57,ca+335,arrivals.get(p['CE'][0],0)+335)
                    if count>cap or delay>old_delay-min_gain_ps:continue
                    key=(delay,count,select,value)
                    if best is None or key<best[0]:best=(key,ds,dt,dinv,cs,ct,cinv,active,select,value)
            if best is None:continue
            _,ds,dt,dinv,cs,ct,cinv,active,select,value=best;new_nodes={};complements={}
            def build(ins,truth,inverse):
                nonlocal maximum,number
                if not ins:return '1' if truth else '0'
                if len(ins)==1 and truth==2:return ins[0]
                maximum+=1;out=maximum;number+=1;name='ng_sync_'+str(number)
                if name in cells:raise ValueError('Synchronous mapper name collision')
                if inverse:
                    c=copy.deepcopy(cells[inverse]);k=int(c['type'][-1]);c['parameters']['INIT']=format(((1<<(1<<k))-1)^int(c['parameters']['INIT'],2),'0'+str(1<<k)+'b');c['connections']['F']=[out];c['attributes']={};complements[out]=ins[0]
                else:
                    k=len(ins);c=dict(type='LUT'+str(k),parameters={'INIT':format(truth,'0'+str(1<<k)+'b')},attributes={},port_directions={**{'I'+str(i):'input' for i in range(k)},'F':'output'},connections={**{'I'+str(i):[b] for i,b in enumerate(ins)},'F':[out]})
                new_nodes[name]=c;arrivals[out]=max(arrivals.get(b,0) for b in ins)+(0 if inverse else 521)
                return out
            nd=build(ds,dt,dinv);nc=build(cs,ct,cinv);newff=copy.deepcopy(ff)
            newff['type']='DFFSE' if active else 'DFFRE';new_control='SET' if active else 'RESET'
            newff['connections'].pop(old_ctrl);newff['port_directions'].pop(old_ctrl)
            newff['connections'].update(D=[nd],**{new_control:[nc]});newff['port_directions'][new_control]='input'
            newff['parameters']['INIT']=ff.get('parameters',{}).get('INIT','1' if old_active else '0')
            symbols=list(dict.fromkeys(b for b in (*boundary,ce,ctrl,q) if b not in ('0','1')))
            # Independent evaluation of actual candidate LUT INITs and FF pins.
            for a in range(1<<len(symbols)):
                vals={b:(a>>i)&1 for i,b in enumerate(symbols)};vals.update({'0':0,'1':1})
                old_d=tt>>sum(vals[b]<<i for i,b in enumerate(boundary))&1
                expected=old_active if vals[ctrl] else old_d if vals[ce] else vals[q]
                for bit,original in complements.items():vals[bit]=1-vals[original]
                for c in new_nodes.values():
                    ins,out,truth=logic(c)
                    if out not in complements:vals[out]=truth>>sum(vals[b]<<i for i,b in enumerate(ins))&1
                observed=active if vals[nc] else vals[nd] if vals[ce] else vals[q]
                if observed!=expected:raise AssertionError('Synchronous next-state proof failed')
            oldff=copy.deepcopy(ff)
            for n in cone:
                old=cells.pop(n)
                for pin,bs in old['connections'].items():
                    for b in bs:
                        if old['port_directions'][pin]=='output':drivers.pop(b,None)
                        else:loads[b].remove((n,pin))
            for name,c in new_nodes.items():
                for pin,bs in c['connections'].items():
                    for b in bs:
                        if c['port_directions'][pin]=='output':drivers[b]=name
                        else:loads[b].append((name,pin))
            for pin in set(oldff['connections'])|set(newff['connections']):
                old_bits=oldff['connections'].get(pin,[]);new_bits=newff['connections'].get(pin,[])
                if old_bits==new_bits or pin=='Q':continue
                for b in old_bits:loads[b].remove((ffname,pin))
                for b in new_bits:loads[b].append((ffname,pin))
            cells.update(new_nodes);cells[ffname]=newff
            current_count=best[0][1]
            records.append(dict(module=mn,flipflop=ffname,old=oldff,new=newff,removed_cells=sorted(cone),new_cells=list(new_nodes),
                                boundary=list(boundary),exhaustive_state_cases=1<<len(symbols),old_estimated_setup_ps=old_delay,new_estimated_setup_ps=best[0][0]))
        final=sum(c['type'] in ('LUT1','LUT2','LUT3','LUT4') for c in cells.values())
        if final>cap:raise AssertionError('Final live LUT budget exceeded')
    return design,dict(schema='synchronous-control-comapping-v1',rewrites=len(records),records=records,
                       scope='Exhaustive local next-state equality with unchanged clock/CE and explicit original INIT; analytical ranking, no physical timing guarantee.')
