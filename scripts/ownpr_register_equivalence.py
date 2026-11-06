"""Inductive state-alias proof for added synchronous register replicas.

Base case: every replica has the same known initialization as its parent.
Step case: under the equality invariant, clocks are identical and CEC proves
equal D/CE/reset inputs. Collapsing replica Qs then proves the original external
and state/hard-input observations. No unrelated state changes are admitted.
"""
import copy
from pathlib import Path
from native_boundary_cec import prove,initial,initial_values,functions

def collapse(before,after,replicas):
    if set(before['modules'])!=set(after['modules']) or 'top' not in before['modules']:raise ValueError('Replica induction requires an unchanged flattened module contract')
    for name,module in before['modules'].items():
        if name=='top':continue
        if not int(module.get('attributes',{}).get('blackbox','0'),2) or module!=after['modules'][name]:raise ValueError('Additional modules must be unchanged blackboxes')
    old=before['modules']['top'];new=after['modules']['top'];cells=new['cells'];old_cells=old['cells']
    if not replicas:raise ValueError('Empty register replica certificate')
    hard=lambda m:{n for n,c in m['cells'].items() if c['type']!='$scopeinfo' and not functions(c)}
    if hard(new)-hard(old)!=set(replicas) or hard(old)-hard(new):raise ValueError('Unexpected state/hard instance changes')
    drivers={}
    for name,cell in cells.items():
        for pin,bits in cell['connections'].items():
            if cell['port_directions'][pin]!='output':continue
            for bit in bits:
                if isinstance(bit,int):
                    if bit in drivers:raise ValueError('Multiple drivers in replica graph')
                    drivers[bit]=(name,pin)
    init=initial_values(new);aliases={};transitions=[]
    for clone,parent in sorted(replicas.items()):
        if parent not in old_cells or parent in replicas or clone not in cells:raise ValueError('Replica parent must be an original register')
        a,b=cells[parent],cells[clone];typ=a['type']
        if typ not in ('DFFRE','DFFSE') or b['type']!=typ:raise ValueError('Only synchronous positive-edge reset/set replicas are modeled')
        control='RESET' if typ=='DFFRE' else 'SET';pins={'D','CE',control,'CLK','Q'}
        expected={p:'output' if p=='Q' else 'input' for p in pins}
        if any(c['port_directions']!=expected or set(c['connections'])!=pins or any(len(bs)!=1 for bs in c['connections'].values()) for c in (a,b)):
            raise ValueError('Unmodeled register pin contract')
        if a.get('parameters',{})!=b.get('parameters',{}) or a['connections']['CLK']!=b['connections']['CLK']:raise ValueError('Replica parameters or clock differ')
        qa,qb=a['connections']['Q'][0],b['connections']['Q'][0]
        ia,ib=initial(init,a,qa),initial(init,b,qb)
        if ia not in ('0','1') or ia!=ib:raise ValueError('Replica initialization is not known and identical')
        if not isinstance(qa,int) or not isinstance(qb,int) or qa==qb or qb in aliases:raise ValueError('Invalid replica state output')
        aliases[qb]=qa
        transitions.append(dict(replica=clone,parent=parent,type=typ,initial_value=ia,
            inputs={p:(a['connections'][p][0],b['connections'][p][0]) for p in ('D','CE',control)}))
    folded=copy.deepcopy(after);module=folded['modules']['top']
    for n in replicas:del module['cells'][n]
    for cell in module['cells'].values():
        cell['connections']={p:[aliases.get(b,b) for b in bs] for p,bs in cell['connections'].items()}
    for collection in ('ports','netnames'):
        for row in module.get(collection,{}).values():row['bits']=[aliases.get(b,b) for b in row['bits']]
    reference=copy.deepcopy(folded);candidate=copy.deepcopy(folded)
    for index,row in enumerate(transitions):
        for pin,(a,b) in row['inputs'].items():
            name=f'__nextgowin_replica_induction_{index}_{pin}'
            if name in module['ports']:raise ValueError('Induction observation name collision')
            reference['modules']['top']['ports'][name]=dict(direction='output',bits=[aliases.get(a,a)])
            candidate['modules']['top']['ports'][name]=dict(direction='output',bits=[aliases.get(b,b)])
    return folded,reference,candidate,transitions

def prove_replicas(before,after,replicas,out,timeout=120):
    folded,reference,candidate,transitions=collapse(before,after,replicas);out=Path(out);out.mkdir(parents=True,exist_ok=False)
    induction=prove(reference,candidate,out/'transition_step',timeout)
    observations=prove(before,folded,out/'observable_equivalence',timeout)
    if (not induction and reference!=candidate) or (not observations and before!=folded) or not all(r['cec']['equivalent'] for r in induction+observations):raise ValueError('Incomplete register induction proof')
    return dict(status='register_replica_invariant_proved',replicas=transitions,transition_step=induction,observable_equivalence=observations,
        transition_structurally_identical=reference==candidate,observations_structurally_identical=before==folded,
        scope='Synchronous DFFRE/DFFSE with identical known initial state and clock; invariant maintained by equal next-state inputs. Physical timing still requires full STA.')
