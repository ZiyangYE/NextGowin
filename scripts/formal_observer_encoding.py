"""Nonintrusive, invertible output observers for equivalence proof views.

Each output is XORed with a fresh common defined input. Original functional
fanouts, state and INIT stay intact. This is proof instrumentation, not RTL
optimization or a substitute for the original-pair initialization gate.
"""
import copy
from restore_equiv_miter_init import init_values


def encode(design,top):
    result=copy.deepcopy(design);m=result['modules'][top];original=design['modules'][top]
    if any(r['direction']=='inout' for r in m['ports'].values()):raise ValueError('Bidirectional proof observers are unsupported')
    values=[b for r in list(m['ports'].values())+list(m['netnames'].values()) for b in r['bits']]
    values.extend(b for c in m['cells'].values() for bs in c['connections'].values() for b in bs)
    nextbit=max((b for b in values if isinstance(b,int)),default=1)+1;rows=[]
    for i,name in enumerate(sorted(n for n,r in original['ports'].items() if r['direction']=='output')):
        raw=list(original['ports'][name]['bits']);width=len(raw);mask=list(range(nextbit,nextbit+width));out=list(range(nextbit+width,nextbit+2*width));nextbit+=2*width
        mask_name='ng_observer_mask_'+str(i);raw_name='$ng_observer_raw$'+str(i);cell_name='$ng_observer_xor$'+str(i)
        if mask_name in m['ports'] or mask_name in m['netnames'] or raw_name in m['netnames'] or cell_name in m['cells']:raise ValueError('Proof observer namespace collision')
        if name not in m['netnames'] or m['netnames'][name]['bits']!=raw:raise ValueError('Port/netname identity mismatch')
        # Retain initialization on the original raw bits, never on encoded bits.
        m['netnames'][raw_name]=dict(m['netnames'][name],hide_name=1,bits=raw)
        m['netnames'][name]=dict(m['netnames'][name],bits=out,attributes={k:v for k,v in m['netnames'][name].get('attributes',{}).items() if k!='init'})
        m['ports'][name]=dict(m['ports'][name],bits=out)
        m['ports'][mask_name]=dict(direction='input',bits=mask);m['netnames'][mask_name]=dict(hide_name=0,bits=mask,attributes={})
        m['cells'][cell_name]=dict(hide_name=1,type='$xor',parameters={k:format(width,'032b') for k in ('A_WIDTH','B_WIDTH','Y_WIDTH')}|{'A_SIGNED':'0','B_SIGNED':'0'},
            attributes={},connections=dict(A=raw,B=mask,Y=out),port_directions=dict(A='input',B='input',Y='output'))
        rows.append(dict(output=name,width=width,mask_input=mask_name,raw_alias=raw_name,observer=cell_name))
    if any(m['cells'][n]!=c for n,c in original['cells'].items()):raise ValueError('Functional cell/fanout modified')
    if init_values(m)!=init_values(original):raise ValueError('Original INIT state changed')
    # Exhaustive one-bit proof in the existing directed X-refinement contract.
    def xor(v,k):return 'x' if v=='x' else str(int(v)^k)
    def agrees(g,t):return g=='x' or g==t
    for g in '01x':
        for t in '01x':
            for k in (0,1):
                if agrees(g,t)!=agrees(xor(g,k),xor(t,k)):raise AssertionError('Observer is not refinement preserving')
    return result,dict(status='invertible_sink_observers_constructed',outputs=rows,defined_common_masks_required=True,
        original_cells_and_fanouts_unchanged=True,original_init_unchanged=True,exhaustive_refinement_cases=18,
        scope='Proof-view output encoding only; original-pair checks, matched INIT checks and induction remain mandatory.')
