"""Expected timing endpoints from logical semantics, independent of reports."""
from ownpr_ff_configuration import FF_CONTROLS


def constant_identity_bits(cells):
    known={'0':0,'1':1};proof={}
    for name,cell in cells.items():
        if cell['type'] in ('GND','VCC'):
            bit=next(iter(cell['connections'].values()))[0];known[bit]=int(cell['type']=='VCC');proof[bit]=[name]
    changed=True
    while changed:
        changed=False
        for name,cell in cells.items():
            if cell['type']!='LUT1' or int(cell['parameters']['INIT'],2)!=2:continue
            source=cell['connections']['I0'][0];dest=cell['connections']['F'][0]
            if source in known and dest not in known:
                known[dest]=known[source];proof[dest]=proof.get(source,[])+[name];changed=True
    return known,proof


def contract(design,mapping):
    cells=design['modules']['top']['cells'];aliases=mapping['instances'];known,proof=constant_identity_bits(cells)
    synchronous=set();asynchronous=set();optional_constants={};clocks=set();hard={}
    if any(c['type'] in ('DPX9B','SDPX9B','MULTALU27X18') for c in cells.values()):
        from ownpr_hard_architecture import HardArchitecture
        hard=HardArchitecture().contract(design)
    def add(name,pin,bit,asynchronous_pin=False):
        endpoint=aliases[name]+'/'+pin
        if bit in known:
            optional_constants[endpoint]=dict(value=known[bit],identity_chain=proof.get(bit,[]),asynchronous=asynchronous_pin);return
        (asynchronous if asynchronous_pin else synchronous).add(endpoint)
    for name,c in cells.items():
        alias=aliases[name]
        if c['type'] in FF_CONTROLS:
            clocks.add((alias,'CLK'))
            for pin in ('D','CE',FF_CONTROLS[c['type']]):
                add(name,pin,c['connections'][pin][0],c['type'] in ('DFFCE','DFFPE') and pin==FF_CONTROLS[c['type']])
        elif c['type']=='OBUF':add(name,'O',c['connections']['I'][0])
        elif name in hard:
            if c['type']=='MULTALU27X18':
                if any(v!='BYPASS' for k,v in c['parameters'].items() if k.endswith('_CLK')):raise ValueError('Registered DSP endpoint contract is not implemented')
                continue
            for pin,values in c['connections'].items():
                if pin in ('CLKA','CLKB'):clocks.add((alias,pin));continue
                if c['port_directions'][pin]!='input':continue
                for index,bit in enumerate(values):
                    label=pin if len(values)==1 else f'{pin}[{index}]'
                    add(name,label,bit,pin in ('RESET','RESETA','RESETB') and c['parameters']['RESET_MODE']=='ASYNC')
    return dict(synchronous=synchronous,asynchronous=asynchronous,optional_constants=optional_constants,clocks=clocks,
        hard_instances=set(hard),constant_bits=known)
