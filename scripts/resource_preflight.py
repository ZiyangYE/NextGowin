"""Count expanded leaf resources before starting the native implementation."""
from collections import Counter
import functools


def count(design,top):
    modules=design['modules'];active=set()
    @functools.lru_cache(None)
    def walk(name):
        if name in active:raise ValueError('Recursive implementation hierarchy')
        active.add(name);result=Counter()
        for cell in modules[name].get('cells',{}).values():
            child=cell['type']
            if child in modules and not int(modules[child].get('attributes',{}).get('blackbox','0'),2):
                result.update(walk(child))
            elif child!='$scopeinfo':result[child]+=1
        active.remove(name)
        return result
    cells=walk(top)
    logic=sum(v for k,v in cells.items() if k.startswith('LUT') or k in ('ALU','ROM16'))
    return dict(leaf_cells=dict(cells),expanded_leaf_count=sum(cells.values()),
                estimated_logic_cells=logic,
                scope='Pre-packing structural count; not a routed resource report. Opaque macros count as one leaf.')
