"""Functional choice extraction for GW5A LUT4 + dedicated mux trees.

Enumerates all Shannon select choices for <=6-variable functions. Choices are
retained until their arrival/area scores are known; support is computed per
output cofactor, never from the physical union of a pair's inputs. This is an
independent architecture adapter, not the EqMap or FAPO authors' implementation.
Delays are explicit analytical synthesis costs; only native P&R signs off timing.
"""
from functools import lru_cache
import itertools
import copy


@lru_cache(maxsize=100000)
def support(truth,k):
    return tuple(i for i in range(k) if any(((truth>>a)^(truth>>(a^(1<<i))))&1 for a in range(1<<k)))


def reduce_function(inputs,truth):
    used=support(truth,len(inputs))
    reduced=sum(((truth>>sum(((a>>i)&1)<<j for i,j in enumerate(used)))&1)<<a for a in range(1<<len(used)))
    return tuple(inputs[i] for i in used),reduced


@lru_cache(maxsize=100000)
def cofactor(truth,k,select,value):
    return sum(((truth>>((a&((1<<select)-1))|((a>>select)<<(select+1))|(value<<select)))&1)<<a for a in range(1<<(k-1)))


def leaf(inputs,truth):
    ins,t=reduce_function(inputs,truth)
    return ('lut',ins,t)


def one_mux_choices(inputs,truth):
    for s in range(len(inputs)):
        rest=inputs[:s]+inputs[s+1:]
        branches=[leaf(rest,cofactor(truth,len(inputs),s,v)) for v in (0,1)]
        if all(len(b[1])<=4 for b in branches):yield ('mux5',inputs[s],*branches)


@lru_cache(maxsize=4096)
def indexed_choices(k,truth):
    inputs=tuple(range(k));plans=list(one_mux_choices(inputs,truth))
    if k==6:
        for s in range(6):
            rest=inputs[:s]+inputs[s+1:]
            branches=[list(one_mux_choices(rest,cofactor(truth,6,s,v))) for v in (0,1)]
            for a,b in itertools.product(*branches):plans.append(('mux6',inputs[s],a,b))
    return tuple(plans)


def choices(inputs,truth):
    inputs,truth=reduce_function(tuple(inputs),truth)
    if len(inputs)<=4:
        yield leaf(inputs,truth);return
    if len(inputs)>6:raise ValueError('Functional choice window exceeds six independent inputs')
    def bind(plan):
        if plan[0]=='lut':return ('lut',tuple(inputs[i] for i in plan[1]),plan[2])
        return (plan[0],inputs[plan[1]],bind(plan[2]),bind(plan[3]))
    plans=indexed_choices(len(inputs),truth)
    if inputs==tuple(range(len(inputs))):yield from plans
    else:
        for plan in plans:yield bind(plan)


def cost(plan,arrivals,lut_ps=521.0,mux_data_ps=200.0,mux_select_ps=250.0):
    if plan[0]=='lut':
        # A constant branch occupies a LUT slot when used under a dedicated mux.
        arrival=max(arrivals[i] for i in plan[1])+lut_ps if plan[1] else 0.0
        return arrival,1
    a,aa=cost(plan[2],arrivals,lut_ps,mux_data_ps,mux_select_ps)
    b,ba=cost(plan[3],arrivals,lut_ps,mux_data_ps,mux_select_ps)
    return max(a+mux_data_ps,b+mux_data_ps,arrivals[plan[1]]+mux_select_ps),aa+ba


def lower(network,input_arrivals=None,lut_ps=521.0,mux_data_ps=200.0,mux_select_ps=250.0,cut_wire_ps=0.0):
    nodes={n['output']:n for n in network['nodes']};arrivals={n:float((input_arrivals or {}).get(n,0)) for n in network['inputs']}
    result=[];records=[];occupied=set(nodes)|set(arrivals);counter=0;ground=None;constant_nets=set()
    def fresh():
        nonlocal counter
        while True:
            counter+=1;n='ng_arch_'+str(counter)
            if n not in occupied:occupied.add(n);return n
    def emit(plan,out,packed=False,fallback=None):
        nonlocal ground
        if plan[0]=='lut':
            inputs=list(plan[1]);truth=plan[2]
            if packed and not inputs:
                # Real LUT leaf for the dedicated mux's mandatory physical slot.
                # Tie its unused pin to GND: an active selector here creates
                # false STA paths through a logically constant LUT on Gowin.
                if ground is None:
                    ground=fresh();result.append(dict(inputs=[],output=ground,truth=0));arrivals[ground]=0.0
                inputs=[ground];truth=3 if truth else 0
            result.append(dict(inputs=inputs,output=out,truth=truth,keep=packed))
            return
        a,b=fresh(),fresh()
        emit(plan[2],a,True,plan[1]);emit(plan[3],b,True,plan[1])
        result.append(dict(inputs=[a,b,plan[1]],output=out,truth=0xCA,native_mux=5 if plan[0]=='mux5' else 6,keep=packed))
    for name in network['topological']:
        n=nodes[name];k=len(n['inputs'])
        plans=list(choices(tuple(n['inputs']),n['truth']))
        if not plans:raise ValueError('No legal GW5A decomposition')
        boundary_arrivals={p:arrivals[p]+(0 if p in constant_nets else cut_wire_ps) for p in n['inputs']}
        scored=[(cost(p,boundary_arrivals,lut_ps,mux_data_ps,mux_select_ps),repr(p),p) for p in plans]
        metrics,_,selected=min(scored,key=lambda x:(x[0][0],x[0][1],x[1]))
        if selected[0]=='lut' and not selected[1]:arrivals[name]=0;constant_nets.add(name)
        else:arrivals[name]=metrics[0]
        emit(selected,name)
        if selected[0]!='lut':records.append(dict(output=name,logical_inputs=k,alternatives=len(plans),lut_slots=metrics[1],arrival_ps=arrivals[name],select=selected[1],kind=selected[0]))
    # All generated leaves are private to their cluster; no cross-cluster sharing
    # of dedicated mux resources is assumed.
    return dict(inputs=list(network['inputs']),outputs=list(network['outputs']),nodes=result,
                topological=[n['output'] for n in result]),dict(schema='gw5a-functional-choices-v1',
                    synthesis_cost_ps=dict(lut=lut_ps,mux_data=mux_data_ps,mux_select=mux_select_ps,cut_wire=cut_wire_ps),
                    delay_scope='Analytical initial costs, not calibrated interconnect or signoff timing',
                    clusters=records,lut_slots=sum(n.get('native_mux') is None and bool(n['inputs']) for n in result),
                    output_arrivals_ps={n:arrivals[n] for n in network['outputs']})


def blif(network):
    lines=['.model functional','.inputs '+' '.join(network['inputs']),'.outputs '+' '.join(network['outputs'])]
    for n in network['nodes']:
        k=len(n['inputs']);lines.append('.names '+' '.join(n['inputs']+[n['output']]))
        if k and n['truth']==0:
            lines.append('-'*k+' 0');continue
        for a in range(1<<k):
            if (n['truth']>>a)&1:lines.append((''.join(str((a>>i)&1) for i in range(k))+' 1') if k else '1')
    return '\n'.join(lines+['.end'])+'\n'


def permute_lut4_pins(network,input_arrivals,pin_delays_ps,wire_ps=672,
                      lut_ps=521,mux_data_ps=200,mux_select_ps=250):
    """Preserve truth tables while assigning late inputs to faster LUT4 pins.

    This is an analytical candidate. The backend may repin cells; actual
    setup/hold signoff remains mandatory. No smaller LUT is padded or enlarged.
    """
    import math
    if len(pin_delays_ps)!=4 or any(not math.isfinite(x) or x<0 for x in pin_delays_ps):
        raise ValueError('Four finite nonnegative LUT4 pin costs required')
    result=copy.deepcopy(network);arrivals={p:float(input_arrivals.get(p,0)) for p in result['inputs']};rows=[]
    constants=set();fastest=sorted(range(4),key=lambda i:(pin_delays_ps[i],i))
    for n in result['nodes']:
        ins=n['inputs'];at=[arrivals[p]+(0 if p in constants else wire_ps) for p in ins]
        if n.get('native_mux'):
            # Dedicated data connections stay inside the packed cluster.
            arrivals[n['output']]=max(arrivals[ins[0]]+mux_data_ps,arrivals[ins[1]]+mux_data_ps,at[2]+mux_select_ps)
        elif len(ins)==4:
            order=[None]*4
            for old,new in zip(sorted(range(4),key=lambda i:(-at[i],i)),fastest):order[new]=old
            truth=n['truth'];permuted=0
            for a in range(16):
                old_assignment=sum(((a>>new)&1)<<old for new,old in enumerate(order))
                permuted|=((truth>>old_assignment)&1)<<a
            before=max(at[i]+pin_delays_ps[i] for i in range(4))
            after=max(at[order[i]]+pin_delays_ps[i] for i in range(4))
            if after<before-1e-6:
                n['inputs']=[ins[i] for i in order];n['truth']=permuted
                rows.append(dict(output=n['output'],old_inputs=ins,new_inputs=n['inputs'],old_arrival_ps=before,new_arrival_ps=after))
            arrivals[n['output']]=min(before,after)
        elif ins:arrivals[n['output']]=max(at)+lut_ps
        else:arrivals[n['output']]=0;constants.add(n['output'])
    return result,dict(scope='Estimated LUT4 pin assignment, native signoff required',pin_delays_ps=pin_delays_ps,changed=rows,output_arrivals_ps={p:arrivals[p] for p in result['outputs']})


def validate_packing(design):
    """Check generated mux topology after every generic native rewrite."""
    counts={5:0,6:0}
    for m in design['modules'].values():
        cells=m.get('cells',{});drivers={};loads={}
        for name,c in cells.items():
            for p,bs in c['connections'].items():
                for b in bs:
                    if c.get('port_directions',{}).get(p)=='output':drivers[b]=name
                    elif c.get('port_directions',{}).get(p)=='input':loads[b]=loads.get(b,0)+1
        for name,c in cells.items():
            if not name.startswith(('ng_acd_','ng_sync_')) or c['type'] not in ('MUX2_LUT5','MUX2_LUT6'):continue
            tier=int(c['type'][-1]);allowed=('LUT1','LUT2','LUT3','LUT4') if tier==5 else ('MUX2_LUT5',)
            branches=[c['connections'][p][0] for p in ('I0','I1')]
            if branches[0]==branches[1] or any(b not in drivers or cells[drivers[b]]['type'] not in allowed or loads.get(b)!=1 for b in branches):
                raise ValueError('Illegal or shared dedicated mux leaf: '+name)
            counts[tier]+=1
    return dict(mux5_checked=counts[5],mux6_checked=counts[6])
