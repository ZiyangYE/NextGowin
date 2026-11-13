"""Exact bounded next-state mapping onto LUT4/mux and synchronous FF resources.

Explores data-only covers and disjoint Boolean AND/OR factorizations for up to
eight boundary signals. Every admitted rewrite proves the actual old/new next
state for all boundary assignments, preserves the FF clock, Q and effective INIT,
and charges the final live LUT count through reference-counted cone recovery.
"""
from collections import defaultdict
from functools import lru_cache
import copy
import itertools
import math
import json
from pathlib import Path
from gw5a_functional_map import reduce_function,choices,cost
from register_control_map import logic,normalize


@lru_cache(maxsize=10000)
def factors(truth,k):
    results=[]
    for count in range(max(1,k-4),min(4,k-1)+1):
        for control in itertools.combinations(range(k),count):
            data=tuple(i for i in range(k) if i not in control);rows=[]
            for a in range(1<<count):
                fixed=sum((a>>i&1)<<p for i,p in enumerate(control))
                rows.append(sum((truth>>(fixed|sum((v>>i&1)<<p for i,p in enumerate(data)))&1)<<v for v in range(1<<len(data))))
            full=(1<<(1<<len(data)))-1
            for active,constant in ((0,0),(1,full)):
                other=set(rows)-{constant}
                if len(other)!=1 or constant not in rows:continue
                dt=next(iter(other));ct=sum(int(r==constant)<<a for a,r in enumerate(rows))
                results.append((active,data,dt,control,ct))
    return tuple(results)


def fold(design,max_changes=512,area_allowance=0.02):
    design=copy.deepcopy(design);history=[];module_reports=[]
    wire=json.loads((Path(__file__).resolve().parents[1]/'configs/gw5a_arch_cost_v2.json').read_text())['cut_wire_ps']
    for mn,m in design['modules'].items():
        if mn.startswith('~') or int(m.get('attributes',{}).get('blackbox','0'),2):continue
        cells=m.get('cells',{});drivers={};loads=defaultdict(list);constants={}
        protected={b for r in m.get('netnames',{}).values() if int(r.get('attributes',{}).get('keep','0'),2) for b in r['bits']}
        protected.update(b for r in m.get('ports',{}).values() for b in r['bits'])
        for name,c in cells.items():
            for p,bs in c['connections'].items():
                for b in bs:
                    if c.get('port_directions',{}).get(p)=='output':drivers[b]=name
                    elif c.get('port_directions',{}).get(p)=='input':loads[b].append((name,p))
            if c['type'] in ('VCC','GND'):
                for b in c['connections']['V' if c['type']=='VCC' else 'G']:constants[b]='1' if c['type']=='VCC' else '0'
        def constant(b):return constants.get(b,b)
        old_count=sum(c['type'] in ('LUT1','LUT2','LUT3','LUT4') for c in cells.values());current=old_count;cap=old_count+math.floor(old_count*area_allowance)
        all_bits=[b for c in cells.values() for bs in c['connections'].values() for b in bs]
        all_bits += [b for r in list(m.get('netnames',{}).values())+list(m.get('ports',{}).values()) for b in r['bits']]
        maximum=max((b for b in all_bits if isinstance(b,int)),default=1);serial=0
        arrivals={'0':0.0,'1':0.0};inactive={'0','1'}|set(constants)
        def incoming(b):return arrivals[b]+(0 if b in inactive else wire)
        for r in m.get('ports',{}).values():
            if r['direction']=='input':arrivals.update({b:0.0 for b in r['bits']})
        todo={}
        for name,c in cells.items():
            if logic(c):todo[name]=c
            else:
                for p,bs in c['connections'].items():
                    if c.get('port_directions',{}).get(p)=='output':arrivals.update({b:0.0 if c['type'] in ('VCC','GND') else 344.0 for b in bs})
        while todo:
            ready=[]
            for name,c in todo.items():
                ins,out,tt=logic(c)
                if not all(b in arrivals for b in ins):continue
                if all(b in inactive for b in ins):arrivals[out]=0.0;inactive.add(out)
                else:arrivals[out]=max(arrivals[ins[0]]+200,arrivals[ins[1]]+200,incoming(ins[2])+250) if c['type'].startswith('MUX') else max((incoming(b) for b in ins),default=0)+521
                ready.append(name)
            if not ready:raise ValueError('Non-acyclic or unknown register mapping boundary')
            for name in ready:del todo[name]
        def delay(b,setup):return 0.0 if b in inactive or constant(b) in ('0','1') else arrivals.get(b,0)+(wire if setup!=57 else 0)+setup
        flops=[n for n,c in cells.items() if c['type'] in ('DFFRE','DFFSE','DFFCE','DFFPE')]
        flops.sort(key=lambda n:-max(delay(b[0],57 if p=='D' else 335) for p,b in cells[n]['connections'].items() if p in ('D','CE','RESET','SET','CLEAR','PRESET')))
        module_start=len(history)
        for name in flops:
            if len(history)-module_start>=max_changes:break
            ff=cells[name];p=ff['connections']
            if int(ff.get('attributes',{}).get('keep','0'),2):continue
            ctrl_pin={'DFFRE':'RESET','DFFSE':'SET','DFFCE':'CLEAR','DFFPE':'PRESET'}[ff['type']]
            active=int(ff['type'] in ('DFFSE','DFFPE'));d,ce,ctrl,q=[constant(p[x][0]) for x in ('D','CE',ctrl_pin,'Q')]
            if ctrl_pin in ('CLEAR','PRESET') and ctrl!='0':continue
            old_delay=max(delay(p['D'][0],57),delay(p['CE'][0],335),delay(p[ctrl_pin][0],335))
            if old_delay<=0:continue
            front=list(dict.fromkeys((d,ce,ctrl,q)));cone=set()
            for _ in range(20):
                changed=False
                for b in sorted(front,key=lambda x:-arrivals.get(x,0)):
                    producer=drivers.get(b)
                    if producer in cone or producer not in cells or b in protected or not logic(cells[producer]):continue
                    c=cells[producer]
                    if int(c.get('attributes',{}).get('keep','0'),2) and not int(c.get('attributes',{}).get('ng_generated','0'),2):continue
                    proposal=list(dict.fromkeys(v for x in front for v in ([constant(i) for i in logic(c)[0]] if x==b else [x])))
                    if sum(x not in ('0','1') for x in proposal)>8:continue
                    front=proposal;cone.add(producer);changed=True;break
                if not changed:break
            boundary=tuple(b for b in front if b not in ('0','1'))
            if not cone or len(boundary)>8:continue
            tt=0
            for a in range(1<<len(boundary)):
                vals={b:a>>i&1 for i,b in enumerate(boundary)};vals.update({'0':0,'1':1})
                def evaluate(b):
                    b=constant(b)
                    if b in vals:return vals[b]
                    ins,_,truth=logic(cells[drivers[b]]);vals[b]=truth>>sum(evaluate(x)<<i for i,x in enumerate(ins))&1;return vals[b]
                value=active if evaluate(ctrl) else evaluate(d) if evaluate(ce) else evaluate(q)
                tt|=value<<a
            boundary,tt=reduce_function(boundary,tt)
            # Candidate tuple: setup delay, active control value, data plan,
            # control plan. Constant control means an unconditional data cover.
            candidates=[]
            def options(ins,truth):
                ins,truth=normalize(list(ins),truth)
                if not ins:return [('wire','1' if truth else '0')]
                if len(ins)==1 and truth==2:return [('wire',ins[0])]
                if len(ins)>6:return []
                plans=list(choices(ins,truth))
                # Phase inversion can be realized by cloning a predecessor LUT,
                # preserving its depth rather than adding an inverter level.
                if len(ins)==1 and truth==1:
                    producer=drivers.get(ins[0]);c=cells.get(producer,{})
                    if c.get('type') in ('LUT1','LUT2','LUT3','LUT4'):plans.append(('inverse',ins[0],producer))
                return plans
            def estimate(plan):
                if plan[0]=='wire':return arrivals.get(plan[1],0),0
                if plan[0]=='inverse':return arrivals.get(plan[1],0),1
                return cost(plan,{b:incoming(b) for b in boundary})
            for dp in options(boundary,tt):candidates.append((estimate(dp)[0]+57,0,dp,('wire','0')))
            for typ,di,dt,ci,ct in factors(tt,len(boundary)):
                for dp,cp in itertools.product(options(tuple(boundary[i] for i in di),dt),options(tuple(boundary[i] for i in ci),ct)):
                    candidates.append((max(estimate(dp)[0]+57,estimate(cp)[0]+(0 if cp[0]=='wire' and cp[1] in inactive else wire)+335),typ,dp,cp))
            candidates.sort(key=lambda x:(x[0],estimate(x[2])[1]+estimate(x[3])[1],repr(x)))
            selected=None
            for estimated,typ,dp,cp in candidates[:12]:
                if estimated>old_delay+1e-6:continue
                new_nodes={};complements={}
                def fresh():
                    nonlocal maximum,serial
                    while True:
                        maximum+=1;serial+=1;cname='ng_sync_'+str(serial)
                        if cname not in cells and cname not in new_nodes:return cname,maximum
                def emit(plan,packed=False,fallback=None):
                    if plan[0]=='wire':return plan[1]
                    cname,out=fresh()
                    if cname in cells:raise ValueError('Register cover name collision')
                    if plan[0]=='inverse':
                        c=copy.deepcopy(cells[plan[2]]);k=int(c['type'][-1]);c['parameters']['INIT']=format(((1<<(1<<k))-1)^int(c['parameters']['INIT'],2),'0'+str(1<<k)+'b');c['connections']['F']=[out];c['attributes']={};complements[out]=plan[1]
                    elif plan[0]=='lut':
                        ins=list(plan[1]);truth=plan[2]
                        if packed and not ins:ins=['0'];truth=3 if truth else 0
                        if not ins:return '1' if truth else '0'
                        k=len(ins);c=dict(type='LUT'+str(k),parameters={'INIT':format(truth,'0'+str(1<<k)+'b')},attributes={'keep':'1'} if packed else {},port_directions={**{'I'+str(i):'input' for i in range(k)},'F':'output'},connections={**{'I'+str(i):[b] for i,b in enumerate(ins)},'F':[out]})
                    else:
                        a=emit(plan[2],True,plan[1]);b=emit(plan[3],True,plan[1]);c=dict(type='MUX2_LUT5' if plan[0]=='mux5' else 'MUX2_LUT6',parameters={},attributes={'keep':'1'} if packed else {},port_directions=dict(I0='input',I1='input',S0='input',O='output'),connections=dict(I0=[a],I1=[b],S0=[plan[1]],O=[out]))
                    c.setdefault('attributes',{})['ng_generated']='1'
                    new_nodes[cname]=c;return out
                nd,nc=emit(dp),emit(cp);new_ctrl='SET' if typ else 'RESET'
                newff=copy.deepcopy(ff);newff['type']='DFFSE' if typ else 'DFFRE'
                newff['connections'].pop(ctrl_pin);newff['port_directions'].pop(ctrl_pin)
                newff['connections'].update(D=[nd],CE=['1'],**{new_ctrl:[nc]});newff['port_directions'][new_ctrl]='input'
                newff['parameters']['INIT']=ff.get('parameters',{}).get('INIT','1' if active else '0')
                delta=defaultdict(int)
                for pin in ('D','CE',ctrl_pin):delta[p[pin][0]]-=1
                for pin in ('D','CE',new_ctrl):delta[newff['connections'][pin][0]]+=1
                for c in new_nodes.values():
                    for pin,bs in c['connections'].items():
                        if c['port_directions'][pin]=='input':
                            for b in bs:delta[b]+=1
                removed=set();queue=list(delta)
                while queue:
                    b=queue.pop();producer=drivers.get(b)
                    if producer in removed or producer not in cells or len(loads[b])+delta[b]>0 or b in protected:continue
                    c=cells[producer]
                    if not logic(c) or (int(c.get('attributes',{}).get('keep','0'),2) and not int(c.get('attributes',{}).get('ng_generated','0'),2)):continue
                    removed.add(producer)
                    for pin,bs in c['connections'].items():
                        if c['port_directions'][pin]=='input':
                            for x in bs:delta[x]-=1;queue.append(x)
                # A live dedicated mux owns its two local input slots. A
                # resynthesis window may cross those slots only if that parent
                # is recovered as part of this same compound transformation.
                exposed=False
                referenced=[b for c in new_nodes.values() for pin,bs in c['connections'].items() if c['port_directions'][pin]=='input' for b in bs]
                referenced += [nd,nc]
                for b in referenced:
                    if any(owner not in removed and pin in ('I0','I1') and cells.get(owner,{}).get('type') in ('MUX2_LUT5','MUX2_LUT6') for owner,pin in loads[b]):exposed=True;break
                if exposed:continue
                count=current-sum(cells[n]['type'].startswith('LUT') for n in removed)+sum(c['type'].startswith('LUT') for c in new_nodes.values())
                if count>cap or (estimated>=old_delay-1e-6 and count>=current):continue
                # Truth proof is against actual candidate nodes/pins, not the
                # selected template's claimed expression or estimated timing.
                new_drivers={logic(c)[1]:c for c in new_nodes.values()}
                for b,old in complements.items():
                    old_ins,_,old_truth=logic(cells[drivers[old]]);new_ins,_,new_truth=logic(new_drivers[b])
                    if old_ins!=new_ins or new_truth!=old_truth^((1<<(1<<len(old_ins)))-1):raise AssertionError('Invalid complement clone')
                for a in range(1<<len(boundary)):
                    vals={b:a>>i&1 for i,b in enumerate(boundary)};vals.update({'0':0,'1':1})
                    for b,old in complements.items():vals[b]=1-vals[old]
                    def evaluate_new(b):
                        if b in vals:return vals[b]
                        ins,_,truth=logic(new_drivers[b]);vals[b]=truth>>sum(evaluate_new(x)<<i for i,x in enumerate(ins))&1;return vals[b]
                    observed=typ if evaluate_new(nc) else evaluate_new(nd)
                    if observed!=(tt>>a&1):raise AssertionError('Actual next-state truth mismatch')
                selected=(newff,new_nodes,removed,count,estimated,len(boundary),complements);break
            if selected is None:continue
            newff,new_nodes,removed,count,estimated,k,complements=selected;oldff=copy.deepcopy(ff)
            for n in removed:
                c=cells.pop(n)
                for pin,bs in c['connections'].items():
                    for b in bs:
                        if c['port_directions'][pin]=='output':drivers.pop(b,None)
                        else:loads[b].remove((n,pin))
            for cname,c in new_nodes.items():
                for pin,bs in c['connections'].items():
                    for b in bs:
                        if c['port_directions'][pin]=='output':drivers[b]=cname
                        else:loads[b].append((cname,pin))
            for pin in set(p)|set(newff['connections']):
                if pin=='Q' or p.get(pin)==newff['connections'].get(pin):continue
                for b in p.get(pin,[]):loads[b].remove((name,pin))
                for b in newff['connections'].get(pin,[]):loads[b].append((name,pin))
            cells.update(new_nodes);cells[name]=newff
            # The complete new cone is private; only its boundary loads change.
            pending=dict(new_nodes)
            for b,old in complements.items():arrivals[b]=arrivals.get(old,0)
            while pending:
                ready=[]
                for cname,c in pending.items():
                    ins,out,truth=logic(c)
                    if out in complements:ready.append(cname);continue
                    if not all(b in arrivals for b in ins):continue
                    if all(b in inactive for b in ins):arrivals[out]=0.0;inactive.add(out)
                    else:arrivals[out]=max(arrivals[ins[0]]+200,arrivals[ins[1]]+200,incoming(ins[2])+250) if c['type'].startswith('MUX') else max(incoming(b) for b in ins)+521
                    ready.append(cname)
                if not ready:raise ValueError('Candidate cover contains a cycle')
                for n in ready:del pending[n]
            history.append(dict(module=mn,flipflop=name,old=oldff,new=newff,removed=sorted(removed),added=list(new_nodes),
                                truth_variables=k,exhaustive_state_cases=1<<k,old_estimated_setup_ps=old_delay,new_estimated_setup_ps=estimated,
                                live_luts_before=current,live_luts_after=count))
            current=count
        final=sum(c['type'] in ('LUT1','LUT2','LUT3','LUT4') for c in cells.values())
        if final!=current or final>cap:raise AssertionError('Final live-area accounting failed')
        module_reports.append(dict(module=mn,initial_luts=old_count,final_luts=final,cap=cap))
    return design,dict(schema='register-nextstate-cover-v1',rewrites=len(history),records=history,modules=module_reports,
                       timing_scope='Analytical LUT/mux and D versus synchronous-control setup costs, without interconnect/signoff',
                       proof_scope='Exhaustive actual local next-state truth, unchanged CLK/Q and effective INIT, and inactive asynchronous controls; no retiming or register removal.')
