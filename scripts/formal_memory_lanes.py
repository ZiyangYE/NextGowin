"""Propose explicit memory lane relations from aligned read-state aliases.

Aliases are proposal provenance, never an equivalence certificate. Every write,
read, initialization and composition obligation remains required afterward.
"""
import copy

STATE={'$dff','$dffe','$sdff','$sdffe','$sdffce'}


def indices(w):
    offset=w.get('offset',0);width=len(w['bits'])
    return [offset+(width-1-i if w.get('upto',0) else i) for i in range(width)]


def read_aliases(module,memory,read_port,width):
    raw=memory['connections']['RD_DATA'][read_port*width:(read_port+1)*width]
    current={b:i for i,b in enumerate(raw) if isinstance(b,int)};result={}
    for stage in range(3):
        for name,w in module['netnames'].items():
            if w.get('hide_name') or name.startswith('$'):continue
            for index,b in zip(indices(w),w['bits']):
                if b in current:result[(name,index,stage)]=current[b]
        next_stage={}
        for c in module['cells'].values():
            if c['type'] not in STATE:continue
            for d,q in zip(c['connections']['D'],c['connections']['Q']):
                if d in current:
                    if q in next_stage and next_stage[q]!=current[d]:raise ValueError('Ambiguous read-state origin')
                    next_stage[q]=current[d]
        current=next_stage
    return result


def propose_lanes(gold,gate,top,groups):
    result=copy.deepcopy(groups);gm=gold['modules'][top];tm=gate['modules'][top]
    drivers={b:(c,i) for c in tm['cells'].values() for p,bs in c['connections'].items() if c['port_directions'][p]=='output' for i,b in enumerate(bs) if isinstance(b,int)}
    def zero_on_write(memory,lane):
        known={};pending=[(memory['connections']['WR_EN'][lane],'1')]
        while pending:
            bit,value=pending.pop()
            if bit in known:continue
            known[bit]=value
            if bit not in drivers:continue
            c,i=drivers[bit]
            if c['type']=='$mux':
                a,b=c['connections']['A'][i],c['connections']['B'][i]
                if (a,b)==('0','1'):pending.append((c['connections']['S'][0],value))
                elif (a,b)==('1','0'):pending.append((c['connections']['S'][0],'0' if value=='1' else '1'))
        def evaluate(bit,depth=8):
            if bit in ('0','1'):return bit
            if bit in known:return known[bit]
            if not depth or bit not in drivers:return None
            c,i=drivers[bit]
            if c['type']=='$mux':
                s=evaluate(c['connections']['S'][0],depth-1)
                if s in ('0','1'):return evaluate(c['connections']['B' if s=='1' else 'A'][i],depth-1)
            return None
        return evaluate(memory['connections']['WR_DATA'][lane])=='0'
    constants={(n,i):b for n,w in tm['netnames'].items() if not w.get('hide_name') for i,b in zip(indices(w),w['bits']) if b in ('0','1')}
    driven={b for p in tm['ports'].values() if p['direction']=='input' for b in p['bits']}
    driven.update(b for c in tm['cells'].values() for p,bs in c['connections'].items() if c['port_directions'][p]=='output' for b in bs)
    consumed={b for p in tm['ports'].values() if p['direction']=='output' for b in p['bits']}
    consumed.update(b for c in tm['cells'].values() for p,bs in c['connections'].items() if c['port_directions'][p]=='input' for b in bs)
    unused_aliases={(n,i) for n,w in tm['netnames'].items() if not w.get('hide_name') for i,b in zip(indices(w),w['bits']) if isinstance(b,int) and b not in driven and b not in consumed}
    for group in result:
        width=group['profile']['WIDTH'];source=gm['cells'][group['gold']]
        for span in group['spans']:
            target=tm['cells'][span['gate']];physical_width=int(target['parameters']['WIDTH'],2)
            ga=read_aliases(gm,source,span['read_port'],width);ta=read_aliases(tm,target,0,physical_width)
            allowed=set(range(span['logical_low'],span['logical_low']+span['width']));mapping={};reverse={};witnesses={};constant_candidates={}
            for key,lane in ga.items():
                if lane not in allowed:continue
                if key in ta:
                    physical=ta[key]
                    if mapping.setdefault(lane,physical)!=physical or reverse.setdefault(physical,lane)!=lane:raise ValueError('Conflicting logical/physical read-lane witnesses')
                    witnesses.setdefault(lane,[]).append(dict(name=key[0],index=key[1],register_stages=key[2]))
                elif key[:2] in constants:constant_candidates.setdefault(lane,set()).add(constants[key[:2]])
                elif key[:2] in unused_aliases:constant_candidates.setdefault(lane,set()).add('unused_alias')
            padding_witnesses={}
            for lane in sorted(allowed-set(mapping)):
                values=constant_candidates.get(lane,set())
                if not values or values-{'0','unused_alias'}:raise ValueError('Missing nonconstant read-lane witness: '+group['gold']+' lane '+str(lane))
                available=[i for i in range(physical_width) if i not in reverse and zero_on_write(target,i)]
                if not available:raise ValueError('No physical zero-padding lane for compacted source constant')
                physical=available[0];mapping[lane]=physical;reverse[physical]=lane
                padding_witnesses[lane]=dict(value='0',physical=physical,basis=sorted(values),source_write_and_init_obligations_still_required=True)
            span['lane_pairs']=[[i,mapping[i]] for i in sorted(mapping)]
            span['physical_width']=physical_width
            span['lane_witnesses']=witnesses;span['compacted_constant_proposals']=padding_witnesses
            span['lane_relation_proved']=False
    return result
