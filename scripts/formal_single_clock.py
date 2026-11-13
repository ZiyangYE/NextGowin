"""Conservative eligibility and state checks for edge-sampled proof models.

This is a proof abstraction, never a hardware rewrite. With one positive-edge
primary clock, no asynchronous state and no clock-as-data, all state holds
between edges. Universal combinational-output checks at each reachable state
cover observations before the first edge and between edges as well.
"""
from restore_equiv_miter_init import init_values

STATE={'$dff','$dffe','$sdff','$sdffe','$sdffce'}
COMB={'$xor','$xnor','$or','$not','$and','$mux','$pmux','$ne','$eq','$eqx','$nex',
      '$reduce_bool','$reduce_and','$reduce_or','$reduce_xor','$reduce_xnor',
      '$add','$lt','$le','$gt','$ge','$shiftx','$shift','$shl','$shr','$sshl','$sshr',
      '$sub','$neg','$mul','$logic_not','$logic_and','$logic_or','$pos','$concat','$slice'}


def audit(module):
    clocks=set();states={}
    for name,c in module['cells'].items():
        if c['type'] in STATE:
            clock=c['connections'].get('CLK',[])
            if len(clock)!=1 or not isinstance(clock[0],int) or int(c['parameters']['CLK_POLARITY'],2)!=1:
                raise ValueError('Only positive-edge nonconstant clocks admitted')
            clocks.add(clock[0]);states[name]=c
        elif c['type'] not in COMB:
            raise ValueError('Unsupported state, primitive or combinational cell: '+c['type'])
    if len(clocks)!=1:raise ValueError('Exactly one state clock required')
    clock=next(iter(clocks))
    ports=[n for n,p in module['ports'].items() if p['direction']=='input' and p['bits']==[clock]]
    if len(ports)!=1:raise ValueError('Clock must be one unambiguous scalar primary input')
    if any(p['direction']=='inout' for p in module['ports'].values()):raise ValueError('Inout not admitted')
    for name,c in module['cells'].items():
        for port,bits in c['connections'].items():
            if clock in bits:
                if not(name in states and port=='CLK' and c['port_directions'][port]=='input'):
                    raise ValueError('Clock-as-data, generated or driven clock not admitted')
    init=init_values(module);qbits={b for c in states.values() for b in c['connections']['Q']}
    if set(init)-qbits:raise ValueError('Defined initialization outside state')
    return dict(status='single_positive_primary_clock_profile',clock_port=ports[0],
                state_cells=len(states),state_bits=sum(len(c['connections']['Q']) for c in states.values()),
                defined_initial_bits=len(init),clock_data_fanouts=0,asynchronous_state_cells=0,
                output_clock_aliases=[n for n,p in module['ports'].items() if p['direction']=='output' and clock in p['bits']],
                scope='Conditional controller edge abstraction only; no array/source composition admission')


def audit_normalized(original,normalized):
    profile=audit(original)
    old={n:c for n,c in original['cells'].items() if c['type'] in STATE}
    new={n:c for n,c in normalized['cells'].items() if c['type']=='$ff'}
    if set(old)!=set(new):raise ValueError('State identity changed during normalization')
    if any(c['type'] not in COMB|{'$ff'} for c in normalized['cells'].values()):
        raise ValueError('Unmodeled normalized cell')
    mapping={};reverse={}
    for n,c in old.items():
        a=c['connections']['Q'];b=new[n]['connections']['Q']
        if len(a)!=len(b):raise ValueError('State width changed')
        for x,y in zip(a,b):
            if mapping.setdefault(x,y)!=y or reverse.setdefault(y,x)!=x:raise ValueError('State mapping not bijective')
    before=init_values(original);after=init_values(normalized)
    if {mapping[b]:v for b,v in before.items()}!=after:raise ValueError('Defined INIT added, lost or changed')
    # Require all original observable ports and widths; bit IDs may renumber.
    if set(original['ports'])!=set(normalized['ports']):raise ValueError('Port inventory changed')
    for n,p in original['ports'].items():
        q=normalized['ports'][n]
        if p['direction']!=q['direction'] or len(p['bits'])!=len(q['bits']):raise ValueError('Port contract changed')
    return dict(profile=profile,status='state_identity_and_original_init_preserved',
                state_bits_bijective=len(mapping),defined_initial_bits_preserved=len(before),
                blanket_init_or_defined_assumptions=False)
