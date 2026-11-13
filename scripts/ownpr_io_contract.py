"""Independent named-port pin and min/max I/O budget coverage.

Port names remain distinct even when RTL aliases several ports to one bit or
constant. This checks the declared external contract, not STA endpoint or
physical electrical realization completeness.
"""
import math,re


def audit(module,cst,sdc):
    ports={}
    for name,row in module['ports'].items():
        width=len(row['bits']);offset=row.get('offset',0)
        for i,bit in enumerate(row['bits']):
            index=offset+(width-1-i if row.get('upto',0) else i)
            label=name if width==1 else f'{name}[{index}]'
            if label in ports:raise ValueError('Duplicate external port name')
            ports[label]=dict(direction=row['direction'],bit=bit)
    locations=re.findall(r'IO_LOC\s+"([^"]+)"\s+(\w+)\s*;',cst)
    pins=dict(locations)
    if len(pins)!=len(locations) or len(set(pins.values()))!=len(pins):raise ValueError('Duplicate pin or port assignment')
    if set(ports)!=set(pins):raise ValueError('Package pin contract differs from complete named interface')
    clocks=re.findall(r'create_clock\s+-name\s+(\w+)\s+-period\s+([\d.]+)\s+\[get_ports\s+\{([^}]+)\}\]',sdc)
    if len(clocks)!=1:raise ValueError('One explicit primary clock required')
    clock,period,clock_port=clocks[0]
    if not math.isfinite(float(period)) or float(period)<=0 or ports.get(clock_port,{}).get('direction')!='input':raise ValueError('Invalid primary clock')
    if any(x in sdc for x in ('set_false_path','set_multicycle_path','create_generated_clock')):raise ValueError('Timing exception needs independent coverage implementation')
    observed={(direction,mode):set() for direction in ('input','output') for mode in ('min','max')}
    commands=re.findall(r'set_(input|output)_delay\s+-(max|min)\s+([-+\d.eE]+)\s+-clock\s+(\w+)\s+\[get_ports\s+\{([^}]+)\}\]',sdc)
    if len(commands)!=len(re.findall(r'\bset_(?:input|output)_delay\b',sdc)):raise ValueError('Unsupported I/O delay command')
    for direction,mode,value,ref,patterns in commands:
        if ref!=clock or not math.isfinite(float(value)) or float(value)!=0:raise ValueError('Expected common zero-delay I/O contract')
        for pattern in patterns.split():
            expression=re.escape(pattern).replace(r'\[\*\]',r'\[\d+\]')
            hits={name for name in ports if re.fullmatch(expression,name)}
            if not hits or any(ports[name]['direction']!=direction or name==clock_port for name in hits):raise ValueError('Unknown or wrong-direction delay selector')
            observed[direction,mode].update(hits)
    for name,row in ports.items():
        if name==clock_port:continue
        if row['direction'] not in ('input','output'):raise ValueError('Inout timing semantics need an explicit implementation')
        for mode in ('min','max'):
            if name not in observed[row['direction'],mode]:raise ValueError('Missing '+mode+' I/O bound for '+name)
    properties=dict(re.findall(r'IO_PORT\s+"([^"]+)"\s+([^;]+);',cst))
    return dict(status='complete_named_pin_and_zero_io_budget_coverage',clock=clock,period_ns=float(period),clock_port=clock_port,
        ports=len(ports),input_bits=sum(p['direction']=='input' for p in ports.values()),output_bits=sum(p['direction']=='output' for p in ports.values()),
        covered_bits={direction+'_'+mode:len(names) for (direction,mode),names in observed.items()},
        explicit_electrical_properties=len(set(properties)&set(ports)),default_electrical_properties=sorted(set(ports)-set(properties)),
        scope='All named external bits, including aliases/constants; exact frozen pins and declared min/max budgets. Native effective constraints, endpoint inventory and physical electrical realization remain separate checks.')
