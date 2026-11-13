"""Transport existing INIT only to identical copied state cells in an equiv miter.

Yosys equiv_miter copies wire names/widths but may omit attributes. This helper
does not choose initial values or modify logic. Missing/ambiguous identity rejects.
"""
import copy,hashlib


def init_values(module):
    result={}
    for row in module['netnames'].values():
        value=row.get('attributes',{}).get('init')
        if value is None:continue
        if len(value)!=len(row['bits']) or set(value)-set('01x'):raise ValueError('Invalid INIT annotation')
        for bit,v in zip(row['bits'],value[::-1]):
            if v=='x':continue
            if not isinstance(bit,int):
                if bit!=v:raise ValueError('INIT on contradictory constant')
                continue
            if bit in result and result[bit]!=v:raise ValueError('Conflicting source INIT aliases')
            result[bit]=v
    return result


def restore(source,target,source_top='equiv',target_top='proof_base'):
    result=copy.deepcopy(target);a=source['modules'][source_top];b=result['modules'][target_top]
    initial=init_values(a);old=init_values(b);mapping={};inverse={};witnesses=set()
    for name,row in b['netnames'].items():
        if name in a['netnames'] and len(row['bits'])==len(a['netnames'][name]['bits']):
            witnesses.update(zip(a['netnames'][name]['bits'],row['bits']))
    # Whole copied wires can contain unused alias positions. Establish the
    # actual used-bit correspondence through unchanged copied cell ports.
    for name,cell in b['cells'].items():
        if name not in a['cells']:continue
        before=a['cells'][name]
        if any(before[k]!=cell[k] for k in ('type','parameters','port_directions')) or set(before['connections'])!=set(cell['connections']):raise ValueError('Copied cell changed')
        for pin,bits in before['connections'].items():
            if len(bits)!=len(cell['connections'][pin]):raise ValueError('Copied cell port width changed')
            for x,y in zip(bits,cell['connections'][pin]):
                if isinstance(x,str):
                    if x!=y:raise ValueError('Copied constant changed')
                    continue
                if not isinstance(y,int):raise ValueError('Copied signal became constant')
                if (x,y) not in witnesses:raise ValueError('Copied signal lacks same-name wire/offset witness')
                if x in mapping and mapping[x]!=y or y in inverse and inverse[y]!=x:raise ValueError('Copied net topology changed')
                mapping[x]=y;inverse[y]=x
    transported={};rows=[]
    for name,cell in b['cells'].items():
        if cell['type']!='$ff':continue
        original=a['cells'].get(name)
        if not original or original['type']!=cell['type'] or original['parameters']!=cell['parameters'] or original['port_directions']!=cell['port_directions']:raise ValueError('State cell identity changed')
        for pin,bits in original['connections'].items():
            if [mapping.get(bit,bit if isinstance(bit,str) else None) for bit in bits]!=cell['connections'][pin]:raise ValueError('State cell wiring changed')
        value=[]
        for x,y in zip(original['connections']['Q'],cell['connections']['Q']):
            v=initial.get(x,'x');value.append(v)
            if v!='x':
                if y in old and old[y]!=v:raise ValueError('Target INIT conflicts with source')
                if y in transported and transported[y]!=v:raise ValueError('Conflicting transported INIT')
                transported[y]=v
        if any(v!='x' for v in value):
            alias='$ng_verified_init$'+hashlib.sha256(name.encode()).hexdigest()[:20]
            if alias in b['netnames']:raise ValueError('INIT transport alias collision')
            b['netnames'][alias]=dict(hide_name=1,bits=list(cell['connections']['Q']),attributes={'init':''.join(reversed(value))})
            rows.append(dict(cell=name,width=len(value),defined_bits=sum(v!='x' for v in value)))
    if any(bit not in transported or transported[bit]!=v for bit,v in old.items()):raise ValueError('Unexplained target initial state')
    if b['cells']!=target['modules'][target_top]['cells'] or b['ports']!=target['modules'][target_top]['ports']:raise ValueError('INIT transport changed logic/interface')
    return result,dict(status='exact_existing_state_init_transported',state_cells=len(rows),defined_state_bits=len(transported),previously_defined_target_bits=len(old),rows=rows,
        scope='Only existing defined source INIT values, through exact same-name state cells and wire/port correspondence. Undefined state remains undefined; no reset assumptions added.')
