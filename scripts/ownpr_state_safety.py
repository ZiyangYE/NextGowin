"""Conservative cell/net metadata guard for state copying and relocation."""

def active(value):
    text=str(value).strip().lower()
    return text not in ('','false','no','none') and set(text)!={'0'}


def protected_cells(design):
    m=design['modules']['top'];forbidden={'async_reg','keep','dont_touch','mark_debug','syn_keep','syn_preserve','preserve','loc','bel','rloc'}
    # A kept wire must remain named and driven; this is distinct from a
    # synchronizer/no-touch constraint. Copying a state while preserving that
    # original wire does not remove or rename it. See check_replicas below.
    net_forbidden=forbidden-{'keep','syn_keep'}
    badbits={b for row in m.get('netnames',{}).values() if any(k.lower() in net_forbidden and active(v) for k,v in row.get('attributes',{}).items()) for b in row['bits']}
    return {n for n,c in m['cells'].items() if any(k.lower() in forbidden and active(v) for k,v in c.get('attributes',{}).items()) or any(b in badbits for bs in c['connections'].values() for b in bs)}


def check_replicas(before,after,replicas):
    from ownpr_existing_state_alias import admissible
    oldnets=before['modules']['top'].get('netnames',{});newnets=after['modules']['top'].get('netnames',{})
    for name,row in oldnets.items():
        if any(k.lower() in ('keep','syn_keep') and active(v) for k,v in row.get('attributes',{}).items()) and newnets.get(name)!=row:
            raise ValueError('Changed or removed a kept original net '+name)
    for d,names in ((before,set(replicas.values())),(after,set(replicas)|set(replicas.values()))):
        if active(d['modules']['top'].get('attributes',{}).get('blackbox','0')):raise ValueError('Opaque top cannot prove state replication')
        cells=d['modules']['top']['cells'];protected=protected_cells(d)
        for name in names:
            if name in protected or not admissible(cells[name]) or set(cells[name].get('parameters',{}))-{'INIT'}:
                raise ValueError('Protected or unmodeled register replica '+name)
