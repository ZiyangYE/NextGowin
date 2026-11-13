"""Conservative bit-dependency proof for unused physical read lanes.

Unknown cells conservatively connect every input to every output. This audit
does not remove any existing padding obligation or admit a storage model.
"""
import collections
from compositional_memory_cut import lane_pairs,profile

STATE={'$ff','$dff','$dffe','$sdff','$sdffe','$sdffce','$adff','$adffe'}


def audit(module,groups):
    adjacent=collections.defaultdict(set)
    def edge(inputs,outputs):
        for b in inputs:
            if isinstance(b,int):adjacent[b].update(x for x in outputs if isinstance(x,int))
    targets={b for p in module['ports'].values() if p['direction']=='output' for b in p['bits'] if isinstance(b,int)}
    for c in module['cells'].values():
        con=c['connections'];inputs=[b for p,bs in con.items() if c['port_directions'][p]=='input' for b in bs];outputs=[b for p,bs in con.items() if c['port_directions'][p]=='output' for b in bs]
        if c['type'].startswith('$mem'):
            targets.update(b for b in inputs if isinstance(b,int))
        if c['type'] in STATE and len(con.get('D',[]))==len(con.get('Q',[])):
            for d,q in zip(con['D'],con['Q']):edge([d],[q])
            edge([b for p,bs in con.items() if c['port_directions'][p]=='input' and p!='D' for b in bs],con['Q'])
            targets.update(b for b in con.get('CLK',[]) if isinstance(b,int))
        elif c['type']=='$mux' and len(con['A'])==len(con['B'])==len(con['Y']):
            for a,b,y in zip(con['A'],con['B'],con['Y']):edge([a,b]+con['S'],[y])
        elif c['type'] in ('$and','$or','$xor','$xnor','$not') and all(len(con[p])==len(con['Y']) for p in ('A','B') if p in con):
            for i,y in enumerate(con['Y']):edge([con[p][i] for p in ('A','B') if p in con],[y])
        else:edge(inputs,outputs)
    checks=[]
    for group in groups:
        for span in group['spans']:
            cell=module['cells'][span['gate']];p=profile(cell);used={y for x,y in lane_pairs(span,group['profile']['WIDTH'],p['WIDTH'])};unused=sorted(set(range(p['WIDTH']))-used)
            if not unused:continue
            starts={cell['connections']['RD_DATA'][rp*p['WIDTH']+i] for rp in range(p['RD_PORTS']) for i in unused};seen=set(starts);queue=collections.deque(starts)
            while queue:
                for b in adjacent.get(queue.popleft(),()):
                    if b not in seen:seen.add(b);queue.append(b)
            violations=sorted(seen&targets)
            checks.append(dict(memory=span['gate'],unused_physical_lanes=unused,tainted_bits=len(seen),reachable_functional_targets=violations))
    return dict(status='padding_read_noninterference_proved' if all(not x['reachable_functional_targets'] for x in checks) else 'padding_reaches_functional_target',
                checks=checks,scope='No data dependency from listed physical read lanes to original outputs, any memory input or state clock; existing proof obligations remain unchanged',model_or_obligation_modified=False)
