"""Experimental array-boundary abstraction with explicit interface obligations.

Passing its controller proof is conditional, NOT full memory/source equivalence.
Storage initialization, per-word induction, clock/collision semantics and composition
must all be admitted separately. No production source-proof gate is replaced.
"""
import collections,copy,hashlib,json,math,re


def number(cell,key):
    value=cell['parameters'][key]
    if not value or set(value)-{'0','1'}:raise ValueError('Non-binary memory parameter '+key)
    return int(value,2)


def profile(cell):
    if cell['type']!='$mem_v2':raise ValueError('Expected collected memory')
    p={k:number(cell,k) for k in ('WIDTH','SIZE','ABITS','RD_PORTS','WR_PORTS','OFFSET','RD_CLK_ENABLE','WR_CLK_ENABLE','WR_CLK_POLARITY','RD_TRANSPARENCY_MASK','RD_COLLISION_X_MASK')}
    if p['OFFSET'] or p['WR_PORTS']!=1 or p['WR_CLK_ENABLE']!=1 or p['RD_CLK_ENABLE'] or p['RD_TRANSPARENCY_MASK'] or p['RD_COLLISION_X_MASK']:raise ValueError('Unsupported array port/clock contract')
    if p['SIZE']<1 or p['SIZE']&(p['SIZE']-1):raise ValueError('Power-of-two addressed arrays only')
    if p['ABITS']<int(math.log2(p['SIZE'])):raise ValueError('Insufficient address width')
    c=cell['connections']
    for key,width in [('RD_ADDR',p['ABITS']*p['RD_PORTS']),('RD_DATA',p['WIDTH']*p['RD_PORTS']),('RD_EN',p['RD_PORTS']),('WR_ADDR',p['ABITS']),('WR_DATA',p['WIDTH']),('WR_EN',p['WIDTH']),('WR_CLK',1)]:
        if len(c[key])!=width:raise ValueError('Memory port width mismatch '+key)
    if c['RD_EN']!=['1']*p['RD_PORTS'] or any(b!='0' for k in ('RD_ARST','RD_SRST') for b in c[k]):raise ValueError('Unsupported asynchronous read controls')
    init=cell['parameters']['INIT']
    if len(init)!=p['WIDTH']*p['SIZE'] or set(init)-set('01x'):raise ValueError('Unmodeled initial memory image')
    return p


def propose(gold,gate,top,bridge,swap_replicas=False,swap_halves=False):
    gm=gold['modules'][top];tm=gate['modules'][top]
    memories={n:c for n,c in gm['cells'].items() if c['type']=='$mem_v2'};physical={n:c for n,c in tm['cells'].items() if c['type']=='$mem_v2'}
    restored=collections.defaultdict(list)
    for r in bridge['restored_memories']:restored[r['module']].append(r)
    groups=[];used=set()
    for name,cell in sorted(memories.items()):
        scope,sep,local=name.rpartition('.');local=local if sep else name;match=re.fullmatch(r'ng_memory_(\d+)',local)
        if not match:raise ValueError('Unknown restored-array identity')
        module=gm['cells'][scope]['attributes']['module'] if scope else top
        declaration=restored[module][int(match[1])];gp=profile(cell)
        if gp['WIDTH']!=declaration['width'] or gp['SIZE']!=declaration['depth']:raise ValueError('Restored geometry differs')
        prefix=(scope+'.' if scope else '')+declaration['net'].lstrip('\\')+'_'
        matches=sorted(n for n in physical if n.startswith(prefix))
        if not matches or used.intersection(matches):raise ValueError('Missing/ambiguous physical memory proposal')
        used.update(matches);profiles=[profile(physical[n]) for n in matches]
        if any(p['RD_PORTS']!=1 or p['SIZE']<gp['SIZE'] for p in profiles):raise ValueError('Unsupported physical read/depth partition')
        spans=[]
        if len(matches)==1 and profiles[0]['WIDTH']>=gp['WIDTH'] and gp['RD_PORTS']==1:
            spans=[dict(gate=matches[0],read_port=0,logical_low=0,width=gp['WIDTH'])]
        elif all(p['WIDTH']==gp['WIDTH'] for p in profiles) and len(matches)==gp['RD_PORTS']:
            order=list(reversed(matches)) if swap_replicas else matches
            spans=[dict(gate=n,read_port=i,logical_low=0,width=gp['WIDTH']) for i,n in enumerate(order)]
        elif gp['RD_PORTS']==1 and sum(p['WIDTH'] for p in profiles)==gp['WIDTH']:
            order=list(reversed(matches)) if swap_halves else matches;offset=0
            for n in order:
                w=profile(physical[n])['WIDTH'];spans.append(dict(gate=n,read_port=0,logical_low=offset,width=w));offset+=w
        else:raise ValueError('Unsupported replica/slice proposal')
        groups.append(dict(gold=name,original_array=declaration['net'],module=module,profile=gp,spans=spans,
            matching_basis='Hierarchy/restored-name proposal only; command and controller proof still required'))
    if used!=set(physical):raise ValueError('Unmatched physical memory')
    return groups


def initial_relation(gold,gate,top,groups):
    gm=gold['modules'][top]['cells'];tm=gate['modules'][top]['cells'];checks=[]
    for group in groups:
        gp=group['profile'];gi=gm[group['gold']]['parameters']['INIT'][::-1];related={};defined=0;refined=0;replicas=0
        for span in group['spans']:
            cell=tm[span['gate']];tp=profile(cell);ti=cell['parameters']['INIT'][::-1]
            for address in range(gp['SIZE']):
                pairs=lane_pairs(span,gp['WIDTH'],tp['WIDTH'])
                for logical,lane in pairs:
                    key=address,logical;g=gi[address*gp['WIDTH']+key[1]];t=ti[address*tp['WIDTH']+lane]
                    if t not in '01':raise ValueError('Undefined physical memory initialization is not yet admitted')
                    if g in '01':
                        if g!=t:raise ValueError('Defined source INIT mismatch')
                        defined+=1
                    else:refined+=1
                    if key in related:
                        if related[key]!=t:raise ValueError('Replicas have inconsistent initial contents')
                        replicas+=1
                    related[key]=t
                for lane in sorted(set(range(tp['WIDTH']))-{p for _,p in pairs}):
                    if ti[address*tp['WIDTH']+lane]!='0':raise ValueError('Padding storage is not initialized zero')
        if len(related)!=gp['WIDTH']*gp['SIZE']:raise ValueError('Logical storage relation omits a bit')
        checks.append(dict(memory=group['gold'],logical_bits=len(related),defined_source_bit_checks=defined,undefined_source_refinement_checks=refined,replica_consistency_checks=replicas))
    return dict(status='initial_storage_relation_checked',checks=checks,full_sequential_equivalence=False)


def lane_pairs(span,logical_width,physical_width):
    pairs=span.get('lane_pairs',[[span['logical_low']+i,i] for i in range(span['width'])])
    if len(pairs)!=span['width'] or len({x for x,_ in pairs})!=len(pairs) or len({y for _,y in pairs})!=len(pairs):raise ValueError('Non-bijective lane span')
    if any(not 0<=x<logical_width or not 0<=y<physical_width for x,y in pairs):raise ValueError('Lane out of range')
    return pairs


def abstract(design,top,groups,role):
    d=copy.deepcopy(design);m=d['modules'][top];cells=m['cells'];ports=m['ports'];netnames=m['netnames']
    # Remove zero-port diagnostic hierarchy cells only; preserve all functional cells.
    for n,c in list(cells.items()):
        if c['type']=='$scopeinfo':
            if c['connections']:raise ValueError('Scope metadata has functional pins')
            del cells[n]
    for obj in list(cells.values())+list(netnames.values()):
        for k in ('src','hdlname'):obj.get('attributes',{}).pop(k,None)
    allbits=[b for c in cells.values() for bs in c['connections'].values() for b in bs]+[b for r in list(ports.values())+list(netnames.values()) for b in r['bits']]
    nextbit=max(b for b in allbits if isinstance(b,int))+1;serial=0;remap={};obligations=[]
    def alloc(w):
        nonlocal nextbit
        bits=list(range(nextbit,nextbit+w));nextbit+=w;return bits
    def port(name,direction,bits):
        if name in ports or name in netnames:raise ValueError('Cut interface name collision')
        ports[name]=dict(direction=direction,bits=list(bits));netnames[name]=dict(hide_name=0,bits=list(bits),attributes={})
    def op(kind,a,b=None):
        nonlocal serial
        width=1 if kind=='$reduce_or' else len(a);out=alloc(width);name='$ng_memory_contract$'+str(serial);serial+=1
        params={'A_SIGNED':'0','A_WIDTH':format(len(a),'032b'),'Y_WIDTH':format(width,'032b')};pins=dict(A=list(a),Y=out);dirs=dict(A='input',Y='output')
        if b is not None:params.update(B_SIGNED='0',B_WIDTH=format(len(b),'032b'));pins['B']=list(b);dirs['B']='input'
        cells[name]=dict(hide_name=1,type=kind,parameters=params,attributes={},connections=pins,port_directions=dirs);return out
    def masked(data,en):return op('$and',data,en if len(en)==len(data) else en*len(data))
    for index,group in enumerate(groups):
        gp=group['profile'];ab=int(math.log2(gp['SIZE']));oracles=[alloc(gp['WIDTH']) for _ in range(gp['RD_PORTS'])]
        for i,bits in enumerate(oracles):port(f'ng_mem_{index}_read_{i}','input',bits)
        if role=='gold':
            source=cells[group['gold']];conn=source['connections']
            for rp,oracle in enumerate(oracles):
                for old,new in zip(conn['RD_DATA'][rp*gp['WIDTH']:(rp+1)*gp['WIDTH']],oracle):
                    if isinstance(old,int):remap[old]=new
        for j,span in enumerate(group['spans']):
            cell=cells[group['gold'] if role=='gold' else span['gate']];cp=profile(cell);c=cell['connections'];rp=span['read_port'] if role=='gold' else 0
            pairs=lane_pairs(span,gp['WIDTH'],cp['WIDTH'] if role=='gate' else span.get('physical_width',span['width']))
            selected=[x if role=='gold' else y for x,y in pairs];w=len(selected);en=[c['WR_EN'][i] for i in selected];data=[c['WR_DATA'][i] for i in selected];active=op('$reduce_or',en)
            base=f'ng_mem_{index}_part_{j}_';clock=c['WR_CLK'] if cp['WR_CLK_POLARITY'] else op('$not',c['WR_CLK'])
            port(base+'write_clock','output',clock);port(base+'write_mask','output',en);port(base+'write_data','output',masked(data,en))
            port(base+'write_address','output',masked(c['WR_ADDR'][:ab],active))
            port(base+'read_address','output',c['RD_ADDR'][rp*cp['ABITS']:rp*cp['ABITS']+ab])
            highwr=op('$reduce_or',c['WR_ADDR'][ab:]) if cp['ABITS']>ab else ['0']
            highrd=op('$reduce_or',c['RD_ADDR'][rp*cp['ABITS']+ab:(rp+1)*cp['ABITS']]) if cp['ABITS']>ab else ['0']
            port(base+'write_high','output',masked(highwr,active));port(base+'read_high','output',highrd)
            unused=sorted(set(range(cp['WIDTH']))-set(selected)) if role=='gate' else []
            pad=[c['WR_DATA'][i] for i in unused];pad_en=[c['WR_EN'][i] for i in unused]
            padding=op('$reduce_or',masked(pad,pad_en)) if pad else ['0'];port(base+'padding_write','output',padding)
            if role=='gate':
                for logical,physical in pairs:
                    old=c['RD_DATA'][physical];new=oracles[span['read_port']][logical]
                    if isinstance(old,int):
                        if old in remap and remap[old]!=new:raise ValueError('Conflicting memory cut driver')
                        remap[old]=new
                for old in [c['RD_DATA'][i] for i in unused]:
                    if isinstance(old,int):remap[old]='0'
            obligations.append(dict(memory=group['gold'],span=span,ports_prefix=base,clock_polarity_normalized=True))
        for name in ([group['gold']] if role=='gold' else [s['gate'] for s in group['spans']]):del cells[name]
    for c in cells.values():
        for pin,bits in c['connections'].items():c['connections'][pin]=[remap.get(b,b) for b in bits]
    for r in list(ports.values())+list(netnames.values()):r['bits']=[remap.get(b,b) for b in r['bits']]
    if any(c['type'].startswith('$mem') for c in cells.values()):raise ValueError('Residual unmodeled memory')
    return d,dict(role=role,command_obligations=obligations,initial_state_modified=False,
        scope='Conditional controller/interface abstraction; raw array reads are common inputs. Full proof additionally requires validated memory relations and a well-founded composition argument.')
