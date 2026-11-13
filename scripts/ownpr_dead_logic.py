"""Conservative combinational liveness cleanup of a mapped checkpoint.

Keep every hard/state cell, explicit kept/debug nets and entire live wide
macros. Caller must run boundary CEC, configuration checks and complete STA.
"""
import copy
from ownpr_mux_macros import tree

COMBINATIONAL={'LUT1','LUT2','LUT3','LUT4','MUX2_LUT5','MUX2_LUT6','MUX2_LUT7'}


def retained(attributes):
    # Unknown spellings/values are conservatively retained.
    return any(str(k).lower() in ('keep','dont_touch','mark_debug') and str(v).strip().lower() not in ('','0','false','no') and set(str(v).strip())!={'0'} for k,v in attributes.items())


def prune(design,layout):
    module=design['modules']['top'];cells=module['cells'];drivers={}
    for name,c in cells.items():
        for pin,bits in c['connections'].items():
            if c['port_directions'][pin]!='output':continue
            for bit in bits:
                if bit in drivers:raise ValueError('Ambiguous driver during dead logic cleanup')
                drivers[bit]=name
    roots=set(n for n,c in cells.items() if c['type'] not in COMBINATIONAL or retained(c.get('attributes',{})))
    for row in module['ports'].values():
        if row['direction']!='input':roots.update(drivers[b] for b in row['bits'] if b in drivers)
    for row in module.get('netnames',{}).values():
        if retained(row.get('attributes',{})):roots.update(drivers[b] for b in row['bits'] if b in drivers)
    groups=[]
    for macro in layout.get('wide_macros',[]):
        spec=tree(design,macro['root'],macro['root_slot']);groups.append(set(spec['leaves'])|set(spec['muxes']))
    live=set();pending=list(roots)
    while True:
        while pending:
            name=pending.pop()
            if name in live:continue
            live.add(name)
            for pin,bits in cells[name]['connections'].items():
                if cells[name]['port_directions'][pin]!='output':pending.extend(drivers[b] for b in bits if b in drivers and drivers[b] not in live)
        additions=set().union(*(g-live for g in groups if g&live)) if groups else set()
        if not additions:break
        pending.extend(additions)
    dead={n for n,c in cells.items() if c['type'] in COMBINATIONAL and n not in live}
    new=copy.deepcopy(design);placed=copy.deepcopy(layout)
    for name in dead:
        row=placed['clusters'].get(name)
        if row and row.get('ff'):raise ValueError('Cannot remove a LUT with a physical FF binding')
        new['modules']['top']['cells'].pop(name)
        placed['clusters'].pop(name,None);placed.get('mux_clusters',{}).pop(name,None)
    placed['wide_macros']=[m for m in placed.get('wide_macros',[]) if m['root'] not in dead]
    placed.update(physical_legality_complete=False,timing_signoff=False)
    return new,placed,dict(removed_cells=sorted(dead),removed_count=len(dead),hard_state_cells_removed=False,explicit_observations_preserved=True,
        clock_endpoints_unchanged=True,scope='Backward structural liveness; no functional unused-input pruning; boundary CEC and new routed STA mandatory')
