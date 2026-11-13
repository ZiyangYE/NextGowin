"""Replace only artificial padding-zero monitors with a noninterference proof.

No original circuit port, cell, state, logical memory command or INIT is removed.
This certifies a projection within the array/controller model, not SDK admission
or full source equivalence.
"""
import copy
from formal_padding_observability import audit
from restore_equiv_miter_init import init_values
from compositional_memory_cut import profile,lane_pairs


def certify(gate,top,groups):
    result=audit(gate['modules'][top],groups)
    if result['status']!='padding_read_noninterference_proved':raise ValueError('Padding reaches original functional observations')
    checks={r['memory']:r for r in result['checks']};removed=[]
    for i,g in enumerate(groups):
        for j,s in enumerate(g['spans']):
            p=profile(gate['modules'][top]['cells'][s['gate']]);used={y for x,y in lane_pairs(s,g['profile']['WIDTH'],p['WIDTH'])};unused=sorted(set(range(p['WIDTH']))-used)
            if not unused:continue
            check=checks.get(s['gate'])
            if not check or check['unused_physical_lanes']!=unused or check['reachable_functional_targets']:raise ValueError('Padding certificate coverage mismatch')
            removed.append(dict(port=f'ng_mem_{i}_part_{j}_padding_write',physical_memory=s['gate'],physical_lanes=unused))
    return dict(status='conditional_padding_projection_authorized',noninterference=result,replaced_observations=removed,
                replacement='Original-array dependency proof replaces auxiliary physical-padding-write-is-zero assertion',
                original_io_and_logical_memory_commands_retained=True,full_source_equivalence_proved=False)


def project(design,top,certificate,role):
    if role not in ('gold','gate'):raise ValueError('Unknown role')
    if certificate.get('status')!='conditional_padding_projection_authorized':raise ValueError('Missing padding proof')
    result=copy.deepcopy(design);before=design['modules'][top];after=result['modules'][top]
    names=[r['port'] for r in certificate['replaced_observations']]
    if len(set(names))!=len(names):raise ValueError('Duplicate projection observation')
    for name in names:
        port=after['ports'].get(name)
        if not name.startswith('ng_mem_') or not name.endswith('_padding_write') or not port or port['direction']!='output' or len(port['bits'])!=1:
            raise ValueError('Projection is not a scalar artificial padding observation')
        if role=='gold' and port['bits']!=['0']:raise ValueError('Unexpected reference padding condition')
        if after['netnames'].get(name,{}).get('bits')!=port['bits']:raise ValueError('Padding observer alias mismatch')
        del after['ports'][name]
        after['netnames'][name]['hide_name']=1
    if after['cells']!=before['cells'] or init_values(after)!=init_values(before):raise ValueError('Projection changed functional graph or INIT')
    if after['ports']!={n:p for n,p in before['ports'].items() if n not in names}:raise ValueError('Non-padding interface changed')
    return result,dict(status='auxiliary_padding_monitor_projection',role=role,replaced_ports=names,
                       original_cells_fanouts_and_init_unchanged=True,other_ports_unchanged=True,
                       proof_scope='Conditional array-model projection only; full composition still required')
