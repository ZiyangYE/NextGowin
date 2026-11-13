"""Conservative three-value analysis proposes, but does not certify, invariants.

Every proposal must pass independent initialized temporal induction. Unknown
operators and correlations reduce precision; they never become assumptions.
"""
import copy,itertools
from restore_equiv_miter_init import init_values

ZERO,ONE,DEFINED,UNKNOWN=1,2,3,7


def values(mask):return [i for i in range(3) if mask&(1<<i)]


def operation(kind,*args):
    result=0
    for v in itertools.product(*(values(a) for a in args)):
        if kind=='not':r=2 if v[0]==2 else 1-v[0]
        elif kind=='and':r=0 if 0 in v else (2 if 2 in v else 1)
        elif kind=='or':r=1 if 1 in v else (2 if 2 in v else 0)
        elif kind=='xor':r=2 if 2 in v else v[0]^v[1]
        elif kind=='mux':r=v[0] if v[2]==0 else (v[1] if v[2]==1 else (v[0] if v[0]==v[1] else 2))
        else:raise ValueError(kind)
        result|=1<<r
    return result


def propose(module):
    if any(len(c['connections']['Q'])!=len(c['connections']['D']) for c in module['cells'].values() if c['type']=='$ff'):
        raise ValueError('Mismatched state widths')
    state={q:d for c in module['cells'].values() if c['type']=='$ff' for q,d in zip(c['connections']['Q'],c['connections']['D'])}
    initialized=init_values(module)
    if set(initialized)-set(state):raise ValueError('INIT outside FF state')
    inputs={b for p in module['ports'].values() if p['direction']=='input' for b in p['bits']}
    drivers={b:(n,p,i) for n,c in module['cells'].items() if c['type']!='$ff' for p,bs in c['connections'].items() if c['port_directions'][p]=='output' for i,b in enumerate(bs)}
    candidates=set(initialized);rounds=[]
    while True:
        cache={};active=set()
        def bit(b):
            if isinstance(b,str):return {'0':ZERO,'1':ONE,'x':4,'z':4}[b]
            if b in inputs or b in candidates:return DEFINED
            if b in state or b not in drivers:return UNKNOWN
            n,p,i=drivers[b]
            if n in active:return UNKNOWN
            if n not in cache:
                active.add(n);cache[n]=cell(module['cells'][n]);active.remove(n)
            return cache[n].get(p,[UNKNOWN]*(i+1))[i]
        def cell(c):
            con=c['connections'];outs={p:[UNKNOWN]*len(bs) for p,bs in con.items() if c['port_directions'][p]=='output'};kind=c['type']
            if 'Y' not in outs:return outs
            width=len(outs['Y']);a=[bit(b) for b in con.get('A',[])];b=[bit(v) for v in con.get('B',[])]
            def extend(v,signed):return (v+[v[-1] if signed and v else ZERO]*width)[:width]
            signed=lambda n:bool(int(c.get('parameters',{}).get(n,'0'),2))
            if kind in ('$and','$or','$xor'):
                is_signed=signed('A_SIGNED') and signed('B_SIGNED');aa=extend(a,is_signed);bb=extend(b,is_signed)
                outs['Y']=[operation(kind[1:],x,y) for x,y in zip(aa,bb)]
            elif kind=='$not':outs['Y']=[operation('not',x) for x in extend(a,signed('A_SIGNED'))]
            elif kind=='$mux' and len(a)==width and len(b)==width and len(con['S'])==1:
                s=bit(con['S'][0]);outs['Y']=[operation('mux',x,y,s) for x,y in zip(a,b)]
            elif kind in ('$reduce_or','$reduce_bool','$logic_not','$reduce_and'):
                k='and' if kind=='$reduce_and' else 'or';acc=ONE if k=='and' else ZERO
                for x in a:acc=operation(k,acc,x)
                if kind=='$logic_not':acc=operation('not',acc)
                outs['Y']=[acc]+[ZERO]*(width-1)
            elif kind in ('$eqx','$nex'):outs['Y']=[DEFINED]+[ZERO]*(width-1)
            elif kind=='$mul' and all(not(x&4) for x in a+b):outs['Y']=[DEFINED]*width
            return outs
        rejected={q:bit(state[q]) for q in sorted(candidates) if bit(state[q])&4}
        rounds.append(dict(candidate_count=len(candidates),rejected_bits=rejected))
        if not rejected:break
        candidates.difference_update(rejected)
    return dict(status='static_definedness_candidates_only',initialized_state_bits=len(initialized),
                candidate_bits=sorted(candidates),rounds=rounds,proof_required=True)


def add_assertion(design,bits,top='product'):
    result=copy.deepcopy(design);m=result['modules'][top]
    if not bits:raise ValueError('Empty definedness observation')
    if len(set(bits))!=len(bits) or set(bits)-set(init_values(m)):
        raise ValueError('Definedness candidates must be distinct originally initialized state bits')
    if any(n.startswith('$ng_defined$') for n in m['cells']) or 'ng_defined_violation' in m['ports']:raise ValueError('Observer namespace collision')
    allbits=[b for c in m['cells'].values() for bs in c['connections'].values() for b in bs]
    nextbit=max(b for b in allbits if isinstance(b,int))+1;flags=[]
    for i,b in enumerate(bits):
        flags.append(nextbit)
        m['cells']['$ng_defined$'+str(i)]=dict(hide_name=1,type='$eqx',parameters={'A_WIDTH':'1','B_WIDTH':'1','Y_WIDTH':'1','A_SIGNED':'0','B_SIGNED':'0'},attributes={},connections={'A':[b],'B':['x'],'Y':[nextbit]},port_directions={'A':'input','B':'input','Y':'output'})
        nextbit+=1
    violation=nextbit;ok=nextbit+1
    m['cells']['$ng_defined$reduce']=dict(hide_name=1,type='$reduce_or',parameters={'A_WIDTH':format(len(bits),'b'),'Y_WIDTH':'1','A_SIGNED':'0'},attributes={},connections={'A':flags,'Y':[violation]},port_directions={'A':'input','Y':'output'})
    m['cells']['$ng_defined$not']=dict(hide_name=1,type='$not',parameters={'A_WIDTH':'1','Y_WIDTH':'1','A_SIGNED':'0'},attributes={},connections={'A':[violation],'Y':[ok]},port_directions={'A':'input','Y':'output'})
    m['cells']['$ng_defined$assert']=dict(hide_name=1,type='$assert',parameters={},attributes={},connections={'A':[ok],'EN':['1']},port_directions={'A':'input','EN':'input'})
    m['ports']['ng_defined_violation']=dict(direction='output',bits=[violation]);m['netnames']['ng_defined_violation']=dict(hide_name=0,bits=[violation],attributes={})
    if init_values(m)!=init_values(design['modules'][top]):raise ValueError('Original INIT modified')
    if any(m['cells'][n]!=c for n,c in design['modules'][top]['cells'].items()):raise ValueError('Functional graph changed')
    return result
