"""Check that auxiliary product-proof outputs cannot modify original logic."""
from restore_equiv_miter_init import init_values


def audit(original, observed, helpers, role):
    if set(original['modules']) != set(observed['modules']):
        raise ValueError('Module inventory changed')
    tops = [n for n,m in original['modules'].items()
            if str(m.get('attributes',{}).get('top','0')).strip('0') == '1']
    if len(tops) != 1:
        raise ValueError('Unique top required')
    top = tops[0]
    for name, module in original['modules'].items():
        if name != top and module != observed['modules'][name]:
            raise ValueError('Non-top module changed')
    a,b = original['modules'][top], observed['modules'][top]
    if {k:v for k,v in a.items() if k not in ('ports','netnames')} != {k:v for k,v in b.items() if k not in ('ports','netnames')}:
        raise ValueError('Functional cells or module metadata changed')
    for section in ('ports','netnames'):
        if any(b[section].get(n) != v for n,v in a[section].items()):
            raise ValueError('Original '+section+' changed')
        expected = {'ng_state_helper_'+str(i) for i in range(len(helpers))}
        if set(b[section])-set(a[section]) != expected:
            raise ValueError('Unexpected observer inventory')
    driven = {v for p in a['ports'].values() if p['direction']=='input' for v in p['bits']}
    driven.update(v for c in a['cells'].values() for p,bs in c['connections'].items()
                  if c['port_directions'][p]=='output' for v in bs)
    for i,h in enumerate(helpers):
        name='ng_state_helper_'+str(i); bits=h[role]
        if b['ports'][name] != dict(direction='output',bits=bits):
            raise ValueError('Observer port mismatch')
        if b['netnames'][name] != dict(hide_name=0,bits=bits,attributes={}):
            raise ValueError('Observer net mismatch')
        if any(isinstance(v,int) and v not in driven for v in bits):
            raise ValueError('Undriven observer')
    if init_values(a) != init_values(b):
        raise ValueError('Initial state changed')
    return dict(status='nonintrusive_product_observers_verified', role=role,
                original_cells=len(a['cells']), original_ports=len(a['ports']),
                auxiliary_observers=len(helpers), functional_cells_and_fanouts_unchanged=True,
                original_ports_netnames_and_init_unchanged=True)
