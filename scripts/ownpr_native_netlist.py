"""Emit the packed initial slice for the native STA/serialization adapter.

This serialization preserves the packed graph; it performs no synthesis.
"""
import re
from ownpr_ff_configuration import FF_CONTROLS


def identifier(name):
    if re.fullmatch(r'[A-Za-z_][A-Za-z0-9_$]*',name):return name
    if not name or any(c.isspace() for c in name) or '\\' in name:raise ValueError('Unsupported escaped identifier')
    return '\\'+name+' '


def inactive_clock_projection(design,contracts,top='top'):
    """Tie only calibrated, completely bypassed DSP clocks to existing GND.

    Otherwise a raw external clock on a disabled macro pin makes the native
    parser replace an explicitly packed IBUF. Functional clock pins are never
    projected. The same contract is recomputed in connectivity readback.
    """
    cells=design['modules'][top]['cells'];grounds=sorted(c['connections']['G'][0] for c in cells.values() if c['type']=='GND')
    rows=[]
    for name,contract in contracts.items():
        for pin in contract['inactive']:
            if pin['reason']!='bypass_clock':continue
            cell=cells[name]
            if cell['type']!='MULTALU27X18' or pin['port']!='CLK' or not grounds:raise ValueError('Unsupported inactive clock projection')
            modes=[v for k,v in cell['parameters'].items() if k.endswith('_CLK')]
            if not modes or any(v!='BYPASS' for v in modes):raise ValueError('Registered DSP clock cannot be projected')
            rows.append(dict(instance=name,port='CLK',index=pin['index'],before=cell['connections']['CLK'][pin['index']],after=grounds[0],reason='calibrated_bypass_clock'))
    return rows


def emit(design,top='top',hard=None):
    module=design['modules'][top];ports=module['ports'];bits={};aliases={}
    hard_contract=hard.contract(design,top) if hard is not None else {}
    projections=inactive_clock_projection(design,hard_contract,top)
    overrides={(r['instance'],r['port'],r['index']):r['after'] for r in projections}
    if hard_contract:
        from gowin_netlist_format import formats
        hard_formats=formats()
    for name,port in ports.items():
        if len(port['bits'])!=1 or port['direction'] not in ('input','output'):
            raise ValueError('Initial native handoff requires scalar mapped ports')
        bit=port['bits'][0]
        if bit in bits:raise ValueError('Aliased external ports require a separate handoff rule')
        bits[bit]=identifier(name)
    integers=sorted({b for c in module['cells'].values() for v in c['connections'].values() for b in v if isinstance(b,int)})
    for bit in integers:
        if bit not in bits:bits[bit]='own_n_'+str(bit)
    allowed={'LUT1','LUT2','LUT3','LUT4','ALU','MUX2_LUT5','MUX2_LUT6','MUX2_LUT7','MUX2_LUT8','IBUF','OBUF','GSR','GND','VCC'}|set(FF_CONTROLS)
    allowed.update(c['primitive'] for c in hard_contract.values())
    lines=['module '+identifier(top)+'('+', '.join(identifier(n) for n in ports)+');']
    lines.extend('  '+p['direction']+' '+identifier(n)+';' for n,p in ports.items())
    lines.extend('  wire '+bits[b]+';' for b in integers if not any(b in p['bits'] for p in ports.values()))
    for index,(name,cell) in enumerate(sorted(module['cells'].items())):
        typ=cell['type'];alias=f'own_i_{index:04d}';aliases[name]=alias
        if typ not in allowed:raise ValueError('Unsupported native handoff primitive '+typ)
        # The native reader turns LUT1 INIT=1 into an absorbed Inv object.
        # Explicit LUT4 truth replication preserves the same physical LUT
        # function with unused inputs tied low and keeps its identity.
        inverter=typ=='LUT1' and int(cell['parameters']['INIT'],2)==1
        if inverter:typ='LUT4'
        connections=[]
        for pin,values in cell['connections'].items():
            if len(values)!=1 and name not in hard_contract:raise ValueError('Unsupported non-scalar primitive pin')
            values=[overrides.get((name,pin,i),bit) for i,bit in enumerate(values)]
            signals=[bits[bit] if isinstance(bit,int) else "1'b"+bit if bit in ('0','1') else None for bit in values]
            if not signals or any(s is None for s in signals):raise ValueError('Unknown constant/empty port in native handoff')
            signal=signals[0] if len(signals)==1 else '{'+', '.join(reversed(signals))+'}'
            connections.append('.'+pin+'('+signal+')')
        if inverter:
            grounds=[c['connections']['G'][0] for c in module['cells'].values() if c['type']=='GND']
            if not grounds:raise ValueError('Inverter serialization requires an existing ground cell')
            connections.extend('.I'+str(i)+'('+bits[grounds[0]]+')' for i in range(1,4))
        lines.append('  '+typ+' '+alias+'('+', '.join(connections)+');')
        for param,value in cell.get('parameters',{}).items():
            if name in hard_contract and isinstance(value,str) and re.fullmatch('[A-Z][A-Z0-9_]*',value):
                default=hard_formats.get(typ,{}).get(param,'')
                if not default.strip().startswith('"'):raise ValueError('Unverified hard string parameter')
                lines.append(f'  defparam {alias}.{param} = "{value}";');continue
            if not isinstance(value,str) or not value or set(value)-{'0','1'}:raise ValueError('Unverified native parameter format')
            if inverter and param=='INIT':value='0101010101010101'
            lines.append(f"  defparam {alias}.{param} = {len(value)}'h{int(value,2):X};")
    lines.append('endmodule')
    from gowin_netlist_format import normalize
    text,formats=normalize('\n'.join(lines)+'\n')
    mapping=dict(instances=aliases,nets={str(b):n.strip().lstrip('\\') for b,n in bits.items()},parameter_formats=formats,
        top=top,packing_changed=False,placement_changed=False,external_ports=list(ports))
    if hard_contract:mapping['hard_inactive_clock_projection']=projections
    return text,mapping
