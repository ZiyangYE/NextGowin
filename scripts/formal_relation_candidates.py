"""Offset-aware, side-only relation proposals; every candidate requires proof."""
from formal_memory_lanes import indices,STATE


def extend_predecessors(gold,gate,relations,max_stages=16):
    """Propose hidden pipeline relations from paired successor state bits.

    No pair is assumed or substituted into functional fanout. Different enables
    or implementation choices may invalidate a proposal; proof must reject it.
    """
    if not 1<=max_stages<=64:raise ValueError('Invalid predecessor depth bound')
    state={role:{q:(c['connections']['D'][i],n,i) for n,c in m['cells'].items() if c['type'] in STATE for i,q in enumerate(c['connections']['Q'])} for role,m in [('gold',gold),('gate',gate)]}
    seen={(a,b) for row in relations for a,b in zip(row['gold'],row['gate'])}
    for name,p in gold['ports'].items():
        q=gate['ports'].get(name)
        if p['direction']=='output' and q and q['direction']=='output' and len(p['bits'])==len(q['bits']):seen.update(zip(p['bits'],q['bits']))
    frontier=sorted(seen,key=repr);extra=[]
    for stage in range(1,max_stages+1):
        next_pairs=[]
        for a,b in frontier:
            if a not in state['gold'] or b not in state['gate']:continue
            ga,gn,gi=state['gold'][a];tb,tn,ti=state['gate'][b]
            if ga not in state['gold'] or tb not in state['gate'] or (ga,tb) in seen:continue
            seen.add((ga,tb));next_pairs.append((ga,tb))
            extra.append(dict(name='state_predecessor:'+gn+':'+str(gi),width=1,gold=[ga],gate=[tb],kind='direct_state_predecessor_candidate',proven=False,stage=stage,
                successor_states=dict(gold=dict(cell=gn,index=gi,q=a),gate=dict(cell=tn,index=ti,q=b))))
        if not next_pairs:break
        frontier=sorted(next_pairs,key=repr)
    return list(relations)+extra,extra


def propose(gold,gate):
    modules={'gold':gold,'gate':gate};drivers={};result=[];seen=set()
    for role,m in modules.items():
        drivers[role]={b for p in m['ports'].values() if p['direction']=='input' for b in p['bits']}
        drivers[role].update(b for c in m['cells'].values() for p,bs in c['connections'].items() if c['port_directions'][p]=='output' for b in bs)
    def add(name,a,b,kind):
        if not a:return
        key=tuple(a),tuple(b)
        if key in seen:return
        seen.add(key);result.append(dict(name=name,width=len(a),gold=a,gate=b,kind=kind))
    for name in sorted(set(gold['netnames'])&set(gate['netnames'])):
        a,b=gold['netnames'][name],gate['netnames'][name]
        if a.get('hide_name') or b.get('hide_name') or name.startswith('$') or name in gold['ports'] or name in gate['ports']:continue
        aa=dict(zip(indices(a),a['bits']));bb=dict(zip(indices(b),b['bits']));pairs=[(aa[i],bb[i]) for i in sorted(set(aa)&set(bb))
            if isinstance(aa[i],int) and isinstance(bb[i],int) and aa[i] in drivers['gold'] and bb[i] in drivers['gate']]
        add(name,[x for x,y in pairs],[y for x,y in pairs],'aligned_public_bit_coordinates')
    for name,p in gold['ports'].items():
        if p['direction']!='input' or not name.startswith('ng_mem_') or '_read_' not in name:continue
        if name not in gate['ports'] or len(p['bits'])!=len(gate['ports'][name]['bits']):raise ValueError('Oracle interface mismatch')
        current={role:{b:i for i,b in enumerate(m['ports'][name]['bits'])} for role,m in modules.items()}
        for stage in (1,2):
            next_stage={};by_origin={}
            for role,m in modules.items():
                next_stage[role]={};by_origin[role]={}
                for c in m['cells'].values():
                    if c['type'] not in STATE:continue
                    for d,q in zip(c['connections']['D'],c['connections']['Q']):
                        if d in current[role]:
                            origin=current[role][d];next_stage[role][q]=origin;by_origin[role].setdefault(origin,set()).add(q)
            for lane in sorted(set(by_origin['gold'])&set(by_origin['gate'])):
                a=by_origin['gold'][lane];b=by_origin['gate'][lane]
                if len(a)==len(b)==1:add(f'{name}:stage{stage}:lane{lane}',list(a),list(b),'common_read_oracle_pipeline_candidate')
            current=next_stage
    return result
