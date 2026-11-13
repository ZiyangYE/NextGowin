"""Reachable three-valued state-domain proposals; SAT must certify each result.

Undefined initial state remains X. A domain {0,X} is never replaced with INIT0.
Unknown operators conservatively produce {0,1,X}.
"""
import copy,collections
from formal_defined_state import operation,UNKNOWN,ZERO,ONE,DEFINED
from restore_equiv_miter_init import init_values


def propose(module,max_rounds=128):
    state={q:d for c in module['cells'].values() if c['type']=='$ff' for q,d in zip(c['connections']['Q'],c['connections']['D'])}
    initial=init_values(module)
    if set(initial)-set(state):raise ValueError('INIT outside state')
    domains={q:({'0':ZERO,'1':ONE}[initial[q]] if q in initial else 4) for q in state}
    inputs={b for p in module['ports'].values() if p['direction']=='input' for b in p['bits']}
    drivers={b:(n,p,i) for n,c in module['cells'].items() if c['type']!='$ff' for p,bs in c['connections'].items() if c['port_directions'][p]=='output' for i,b in enumerate(bs)}
    rounds=[]
    for iteration in range(max_rounds):
        cache={};active=set()
        def bit(b):
            if isinstance(b,str):return {'0':ZERO,'1':ONE,'x':4,'z':4}[b]
            if b in domains:return domains[b]
            if b in inputs:return DEFINED
            if b not in drivers:return UNKNOWN
            n,p,i=drivers[b]
            if n in active:return UNKNOWN
            if n not in cache:active.add(n);cache[n]=cell(module['cells'][n]);active.remove(n)
            return cache[n][p][i]
        def cell(c):
            con=c['connections'];outs={p:[UNKNOWN]*len(bs) for p,bs in con.items() if c['port_directions'][p]=='output'};kind=c['type']
            if 'Y' not in outs:return outs
            width=len(outs['Y']);a=[bit(x) for x in con.get('A',[])];b=[bit(x) for x in con.get('B',[])]
            signed=lambda n:bool(int(c.get('parameters',{}).get(n,'0'),2))
            def extend(v,s):return (v+[v[-1] if s and v else ZERO]*width)[:width]
            if kind in ('$and','$or','$xor'):
                s=signed('A_SIGNED') and signed('B_SIGNED');outs['Y']=[operation(kind[1:],x,y) for x,y in zip(extend(a,s),extend(b,s))]
            elif kind=='$not':outs['Y']=[operation('not',x) for x in extend(a,signed('A_SIGNED'))]
            elif kind=='$mux' and len(a)==len(b)==width and len(con['S'])==1:
                s=bit(con['S'][0]);outs['Y']=[operation('mux',x,y,s) for x,y in zip(a,b)]
            elif kind in ('$reduce_or','$reduce_bool','$logic_not','$reduce_and'):
                k='and' if kind=='$reduce_and' else 'or';acc=ONE if k=='and' else ZERO
                for x in a:acc=operation(k,acc,x)
                if kind=='$logic_not':acc=operation('not',acc)
                outs['Y']=[acc]+[ZERO]*(width-1)
            elif kind in ('$eqx','$nex'):outs['Y']=[DEFINED]+[ZERO]*(width-1)
            elif kind=='$mul' and all(not(x&4) for x in a+b):outs['Y']=[DEFINED]*width
            return outs
        updated={q:domains[q]|bit(d) for q,d in state.items()};changed=sum(updated[q]!=domains[q] for q in state);rounds.append(changed);domains=updated
        if not changed:break
    else:raise ValueError('State-domain analysis did not converge within bound')
    restricted={q:m for q,m in sorted(domains.items()) if m!=UNKNOWN}
    return dict(status='reachable_state_domains_proposed_not_proved',domains=restricted,
                state_bits=len(state),original_defined_initial_bits=len(initial),restricted_bits=len(restricted),
                domain_counts=dict(collections.Counter(map(str,restricted.values()))),round_changes=rounds,
                encoding={'1':['0'],'2':['1'],'3':['0','1'],'4':['X'],'5':['0','X'],'6':['1','X'],'7':['0','1','X']},
                original_initialization_modified=False,proof_required=True)


def add_assertion(design,domains,top='product'):
    result=copy.deepcopy(design);m=result['modules'][top];state={q for c in m['cells'].values() if c['type']=='$ff' for q in c['connections']['Q']}
    domains={int(q):v for q,v in domains.items()}
    if not domains or set(domains)-state or any(type(v)!=int or not 1<=v<7 for v in domains.values()):raise ValueError('Invalid state-domain candidate')
    if any(n.startswith('$ng_values$') for n in m['cells']):raise ValueError('Value-monitor namespace collision')
    nxt=1+max(b for c in m['cells'].values() for bs in c['connections'].values() for b in bs if isinstance(b,int));flags=[]
    for q,mask in sorted(domains.items()):
        for i,value in enumerate(('0','1','x')):
            if mask>>i&1:continue
            flags.append(nxt);m['cells'][f'$ng_values${q}${i}']=dict(type='$eqx',hide_name=1,attributes={},parameters={'A_WIDTH':'1','B_WIDTH':'1','Y_WIDTH':'1','A_SIGNED':'0','B_SIGNED':'0'},connections={'A':[q],'B':[value],'Y':[nxt]},port_directions={'A':'input','B':'input','Y':'output'});nxt+=1
    violation=nxt;ok=nxt+1
    m['cells']['$ng_values$reduce']=dict(type='$reduce_or',hide_name=1,attributes={},parameters={'A_WIDTH':format(len(flags),'b'),'Y_WIDTH':'1','A_SIGNED':'0'},connections={'A':flags,'Y':[violation]},port_directions={'A':'input','Y':'output'})
    m['cells']['$ng_values$not']=dict(type='$not',hide_name=1,attributes={},parameters={'A_WIDTH':'1','Y_WIDTH':'1','A_SIGNED':'0'},connections={'A':[violation],'Y':[ok]},port_directions={'A':'input','Y':'output'})
    m['cells']['$ng_values$assert']=dict(type='$assert',hide_name=1,attributes={},parameters={},connections={'A':[ok],'EN':['1']},port_directions={'A':'input','EN':'input'})
    if 'ng_value_violation' in m['ports'] or 'ng_value_violation' in m['netnames']:raise ValueError('Port collision')
    m['ports']['ng_value_violation']=dict(direction='output',bits=[violation]);m['netnames']['ng_value_violation']=dict(hide_name=0,bits=[violation],attributes={})
    if init_values(m)!=init_values(design['modules'][top]) or any(m['cells'][n]!=c for n,c in design['modules'][top]['cells'].items()):raise ValueError('Functional state changed')
    return result
