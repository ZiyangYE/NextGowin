"""Cycle-exact output predicate cache using original registers' next states.

Restricted to <=6 same-clock DFFRE outputs, known initialization and a
synchronous output observation contract. Arbitrary nonconstant functions
require explicit opt-in; the default retains the minterm mapper. No asynchronous waveform or
metastability equivalence is claimed. Physical admission requires full IO STA.
"""
import copy
from collections import defaultdict
from ownpr_multilevel_cut import Cuts
from native_boundary_cec import initial,initial_values
from register_control_map import logic,normalize


def plan(design,obuf,allow_general=False):
    module=design['modules']['top'];cells=module['cells'];buf=cells[obuf]
    if buf['type']!='OBUF' or len(buf['connections']['I'])!=1 or len(buf['connections']['O'])!=1:raise ValueError('Scalar OBUF required')
    if any(buf['connections']['O'][0] in bs for c in cells.values() for p,bs in c['connections'].items() if c['port_directions'][p]=='input'):raise ValueError('Output pad feedback is outside cache proof scope')
    model=Cuts(design,k=6);frontier={};seen=set();pending=list(buf['connections']['I'])
    while pending:
        bit=pending.pop()
        if bit in seen or bit in model.constants:continue
        seen.add(bit);name=model.drivers.get(bit)
        if name is None:raise ValueError('Output cone contains a primary or unknown input')
        cell=cells[name]
        if cell['type']=='DFFRE':
            for key,value in cell.get('attributes',{}).items():
                text=str(value).strip().lower()
                if key.lower()=='async_reg' and text not in ('','false','no') and any(ch!='0' for ch in text):raise ValueError('Synchronizer state cannot be cached')
            from ownpr_existing_state_alias import admissible
            if not admissible(cell):raise ValueError('Unsupported source state attributes')
            if cell['connections'].get('Q')!=[bit] or set(cell['connections'])!={'D','Q','CLK','CE','RESET'} or any(len(v)!=1 for v in cell['connections'].values()) or set(cell.get('parameters',{}))-{'INIT'}:raise ValueError('Unsupported state boundary')
            frontier[bit]=name
        elif name in model.functions:pending.extend(model.functions[name][0])
        else:raise ValueError('Output cone is not bounded by synchronous DFFRE state')
    if not 1<=len(frontier)<=6:raise ValueError('Output state frontier exceeds bounded proof scope')
    root=model.drivers[buf['connections']['I'][0]]
    cut=dict(cut_bits=[buf['connections']['I'][0]],truth_hex='0x2') if cells[root]['type']=='DFFRE' else model.function(root,set(frontier))
    if not cut['cut_bits']:raise ValueError('Constant output does not need a state cache')
    word=int(cut['truth_hex'],16)
    general=bool(word==0 or word&(word-1))
    if general and not allow_general:raise ValueError('Only single-minterm output predicates supported')
    match=word.bit_length()-1;states=[];inits=initial_values(module);groups=defaultdict(list)
    canonical=lambda b:model.constants.get(b,b)
    for index,bit in enumerate(cut['cut_bits']):
        name=frontier[bit];cell=cells[name];init=initial(inits,cell,bit)
        if init not in ('0','1'):raise ValueError('Unknown source initialization')
        row=dict(name=name,q=bit,d=canonical(cell['connections']['D'][0]),ce=canonical(cell['connections']['CE'][0]),reset=canonical(cell['connections']['RESET'][0]),clock=cell['connections']['CLK'][0],initial=int(init),target=None if general else (match>>index)&1,index=index)
        states.append(row);groups[row['ce'],row['reset']].append(index)
    clocks={r['clock'] for r in states}
    if len(clocks)!=1 or next(iter(clocks)) in model.constants:raise ValueError('Mixed or constant clocks')
    init_address=sum(r['initial']<<r['index'] for r in states);output_initial=(word>>init_address)&1
    if output_initial!=0:raise ValueError('New IO register INIT=1 is not admitted')
    variables=set()
    for r in states:
        for pin in ('q','d','ce','reset'):
            b=r[pin]
            if b not in ('0','1'):
                if type(b)!=int:raise ValueError('Unknown-state input to next-state function')
                variables.add(b)
    if len(variables)>18:raise ValueError('Next-state proof needs a larger SAT backend')
    return dict(obuf=obuf,root=root,old_output_bit=buf['connections']['I'][0],clock=next(iter(clocks)),states=states,groups=[dict(ce=k[0],reset=k[1],indices=v) for k,v in groups.items()],truth_hex=hex(word),output_initial=output_initial,variables=sorted(variables),general_function=general,requires_synchronous_output_contract=True)


def build(design,obuf,allow_general=False):
    info=plan(design,obuf,allow_general);after=copy.deepcopy(design);module=after['modules']['top'];cells=module['cells'];model=Cuts(design,k=6)
    constants={v:next((b for b,x in model.constants.items() if x==v and isinstance(b,int)),v) for v in ('0','1')}
    bits=[b for c in cells.values() for bs in c['connections'].values() for b in bs]+[b for p in module['ports'].values() for b in p['bits']]+[b for n in module.get('netnames',{}).values() for b in n['bits']]
    next_bit=max(b for b in bits if isinstance(b,int))+1;added=[];cache={};index=0
    def allocate():
        nonlocal next_bit
        b=next_bit;next_bit+=1;return b
    def name():
        nonlocal index
        while 'ng_output_state_'+str(index) in cells:index+=1
        n='ng_output_state_'+str(index);index+=1;return n
    def emit(inputs,truth):
        normalized=[model.constants.get(b,b) for b in inputs];support,word=normalize(normalized,truth)
        if not support:return constants[str(word)]
        if len(support)==1 and word==2:return support[0]
        key=tuple(support),word
        if key in cache:return cache[key]
        if len(support)>4:raise ValueError('Unmapped next-state primitive')
        n=name();out=allocate();cells[n]=dict(type='LUT'+str(len(support)),parameters={'INIT':format(word,f'0{1<<len(support)}b')},attributes={},port_directions={**{'I'+str(i):'input' for i in range(len(support))},'F':'output'},connections={**{'I'+str(i):[b] for i,b in enumerate(support)},'F':[out]});added.append(n);cache[key]=out;return out
    def conjunction(inputs):
        while len(inputs)>4:inputs=[emit(inputs[:4],1<<15),*inputs[4:]]
        return emit(inputs,1<<((1<<len(inputs))-1))
    def predicate(rows,pin):
        leaves=[]
        for offset in range(0,len(rows),4):
            chunk=rows[offset:offset+4];target=sum(r['target']<<i for i,r in enumerate(chunk));leaves.append(emit([r[pin] for r in chunk],1<<target))
        return conjunction(leaves)
    outputs=[]
    for group in ([] if info['general_function'] else info['groups']):
        rows=[info['states'][i] for i in group['indices']];reset_value=int(all(r['target']==0 for r in rows));ce,reset=group['ce'],group['reset']
        if reset=='1':outputs.append(constants[str(reset_value)]);continue
        q=predicate(rows,'q') if ce!='1' else constants['0'];d=predicate(rows,'d') if ce!='0' else constants['0']
        truth=sum((reset_value if a&8 else ((a>>1)&1 if a&4 else a&1))<<a for a in range(16))
        outputs.append(emit([q,d,ce,reset],truth))
    if info['general_function']:
        states={r['q']:r for r in info['states']};support_cache={};lifted={}
        def mapped(inputs,word):
            inputs,word=normalize([model.constants.get(b,b) for b in inputs],word)
            if len(inputs)<=4:return emit(inputs,word)
            width=1<<(len(inputs)-1);mask=(1<<width)-1
            lo=mapped(inputs[:-1],word&mask);hi=mapped(inputs[:-1],word>>width)
            return emit([lo,hi,inputs[-1]],0xCA)
        def support(bit):
            if bit in model.constants:return set()
            if bit in support_cache:return support_cache[bit]
            n=model.drivers.get(bit)
            if n not in model.functions:return {bit}
            ins,_,word=model.functions[n];ins,_=normalize([model.constants.get(b,b) for b in ins],word)
            result=set().union(*(support(b) for b in ins)) if ins else set();support_cache[bit]=result;return result
        def select(q,d,ce,reset,reset_value):
            truth=sum((reset_value if a&8 else ((a>>1)&1 if a&4 else a&1))<<a for a in range(16))
            return emit([q,d,ce,reset],truth)
        def lift(bit):
            if bit in model.constants:return constants[model.constants[bit]]
            if bit in lifted:return lifted[bit]
            if bit in states:
                r=states[bit];value=select(bit,r['d'],r['ce'],r['reset'],0)
            else:
                n=model.drivers[bit];boundary=support(bit);group=[states[b] for b in sorted(boundary)]
                if group and len(group)<=4 and len({(r['ce'],r['reset']) for r in group})==1:
                    cut=model.function(n,boundary);rows=[states[b] for b in cut['cut_bits']];word=int(cut['truth_hex'],16)
                    value=select(mapped([r['q'] for r in rows],word),mapped([r['d'] for r in rows],word),group[0]['ce'],group[0]['reset'],word&1)
                else:
                    ins,_,word=model.functions[n];ins,word=normalize([model.constants.get(b,b) for b in ins],word)
                    value=mapped([lift(b) for b in ins],word)
            lifted[bit]=value;return value
        if support(info['old_output_bit'])<=set(states):data=lift(info['old_output_bit'])
        else:data=mapped([lift(r['q']) for r in info['states']],int(info['truth_hex'],16))
    else:data=conjunction(outputs)
    ff=name();q=allocate()
    cells[ff]=dict(type='DFFRE',parameters={'INIT':'0'},attributes={},port_directions={'D':'input','CLK':'input','CE':'input','RESET':'input','Q':'output'},connections={'D':[data],'CLK':[info['clock']],'CE':[constants['1']],'RESET':[constants['0']],'Q':[q]})
    cells[obuf]['connections']['I']=[q]
    proof=verify(design,after,obuf,ff,allow_general)
    return after,dict(**info,new_ff=ff,new_q=q,next_output=data,added_luts=added,proof=proof,interface_cycles_changed=False,physical_placement_pending=True,full_io_sta_required=True,continuous_time_waveform_equivalence=False)


def verify(before,after,obuf,ff,allow_general=False):
    info=plan(before,obuf,allow_general);old=before['modules']['top'];new=after['modules']['top'];cells=new['cells'];cell=cells[ff]
    if set(before['modules'])!=set(after['modules']) or any(before['modules'][n]!=after['modules'][n] for n in before['modules'] if n!='top') or {k:v for k,v in old.items() if k!='cells'}!={k:v for k,v in new.items() if k!='cells'}:raise ValueError('Original module contract changed')
    if ff in old['cells'] or set(cell['connections'])!={'D','Q','CLK','CE','RESET'} or any(len(v)!=1 for v in cell['connections'].values()):raise ValueError('Invalid new state shape')
    occupied={b for c in old['cells'].values() for bs in c['connections'].values() for b in bs}|{b for p in old['ports'].values() for b in p['bits']}|{b for n in old.get('netnames',{}).values() for b in n['bits']}
    for n in set(cells)-set(old['cells']):
        for pin,bs in cells[n]['connections'].items():
            if cells[n]['port_directions'][pin]=='output' and any(type(b)!=int or b in occupied for b in bs):raise ValueError('Added output aliases an original signal')
    for n,c in old['cells'].items():
        expected=copy.deepcopy(c)
        if n==obuf:expected['connections']['I']=cell['connections']['Q']
        if cells.get(n)!=expected:raise ValueError('Original logic/state changed: '+n)
    model=Cuts(after,k=6);constant=lambda b:model.constants.get(b,b)
    if cell['type']!='DFFRE' or cell.get('parameters')!={'INIT':'0'} or cell['connections']['CLK']!=[info['clock']] or constant(cell['connections']['CE'][0])!='1' or constant(cell['connections']['RESET'][0])!='0':raise ValueError('Invalid output cache register')
    q=cell['connections']['Q'][0]
    loads=[(n,p) for n,c in cells.items() for p,bs in c['connections'].items() if c['port_directions'][p]=='input' and q in bs]
    if loads!=[(obuf,'I')]:raise ValueError('New state is not isolated to the requested output')
    if any(cells[n]['type'] not in ('LUT1','LUT2','LUT3','LUT4') for n in set(cells)-set(old['cells'])-{ff}):raise ValueError('Unexpected added state/hard cell')
    data=cell['connections']['D'][0];word=int(info['truth_hex'],16);variables=info['variables'];memo_base={b:int(v) for b,v in model.constants.items()}
    for assignment in range(1<<len(variables)):
        values={**memo_base,**{b:(assignment>>i)&1 for i,b in enumerate(variables)}};address=0
        for r in info['states']:
            nxt=0 if values[r['reset']] else values[r['d']] if values[r['ce']] else values[r['q']]
            address|=nxt<<r['index']
        expected=(word>>address)&1;active=set()
        def evaluate(bit):
            if bit in values:return values[bit]
            n=model.drivers.get(bit)
            if n in active or n not in model.functions:raise ValueError('Unexpected state or cycle in new next-state logic')
            active.add(n);inputs,_,truth=model.functions[n];value=(truth>>sum(evaluate(b)<<i for i,b in enumerate(inputs)))&1;active.remove(n);values[bit]=value;return value
        if evaluate(data)!=expected:raise ValueError('Inductive next-output equality failed')
    return dict(status='exhaustive_output_state_invariant_verified',base_case=True,step_vectors=1<<len(variables),abstract_variables=len(variables),original_state_and_logic_unchanged=True,reset_priority='synchronous RESET before CE',invariant='new_Q = original_output_function(original_Q)',scope='Two-valued synchronous cycle equivalence under declared INIT/GSR behavior; not analog waveform, CDC or physical timing proof')
