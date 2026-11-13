"""Duplicate an entire calibrated wide LUT tree for selected data sinks.

The original tree and its dedicated links stay in place. A copy occupies a
free CFU, preserves truth tables and selector inputs, and must pass CEC/STA.
"""
import copy
from ownpr_mux_macros import tree,validate


def replicate(design,placed,device,source,sinks,legalize=False,protected_cells=()):
    cells=design['modules']['top']['cells'];macros=[m for m in placed.get('wide_macros',[]) if m['root']==source]
    if len(macros)!=1 or cells[source]['type'] not in ('MUX2_LUT5','MUX2_LUT6','MUX2_LUT7'):raise ValueError('Only complete single-CFU wide macro roots may be copied')
    validate(design,placed,device);macro=macros[0];spec=tree(design,source,macro['root_slot']);members=set(spec['leaves'])|set(spec['muxes']);old_bit=cells[source]['connections']['O'][0]
    if not sinks or len(sinks)!=len(set(map(tuple,sinks))):raise ValueError('Missing/duplicate macro copy sink')
    targets=[]
    for name,pin in sinks:
        ordinary=name in placed['clusters'] and cells[name]['type'] in ('LUT1','LUT2','LUT3','LUT4') and pin in ('I0','I1','I2','I3')
        selector=name in placed.get('mux_clusters',{}) and cells[name]['type'] in ('MUX2_LUT5','MUX2_LUT6','MUX2_LUT7') and pin=='S0'
        arithmetic=name in placed.get('arithmetic_clusters',{}) and cells[name]['type']=='ALU' and pin in ('I0','I1','I3')
        if not (ordinary or selector or arithmetic) or cells[name]['connections'].get(pin)!=[old_bit]:raise ValueError('Macro copy sink is not an ordinary input, ALU data input or selector edge')
        point=placed['clusters'][name] if ordinary else placed['arithmetic_clusters'][name] if arithmetic else placed['mux_clusters'][name];targets.append((point['row'],point['column']))
    occupied={(r['row'],r['column']) for kind in ('clusters','mux_clusters','arithmetic_clusters') for r in placed.get(kind,{}).values()}
    for r in placed.get('hard_clusters',{}).values():occupied.update((r['row'],r['column']+i) for i in range(3))
    points={n:(r['row'],r['column']) for kind in ('clusters','mux_clusters','arithmetic_clusters','hard_clusters') for n,r in placed.get(kind,{}).items()}
    for row in placed['clusters'].values():
        if row.get('ff'):points[row['ff']]=(row['row'],row['column'])
    for n,row in placed['fixed_io'].items():points[n]=tuple(v-1 for v in row['native_ports']['out'][:2])
    drivers={b:n for n,c in cells.items() for p,bs in c['connections'].items() if c['port_directions'][p]=='output' for b in bs}
    internal={b for n in members for p,bs in cells[n]['connections'].items() if cells[n]['port_directions'][p]=='output' for b in bs}
    inputs={b for n in members for p,bs in cells[n]['connections'].items() if cells[n]['port_directions'][p]=='input' for b in bs if b not in internal}
    anchors=[points[drivers[b]] for b in inputs if drivers.get(b) in points];distance=lambda a,b:abs(a[0]-b[0])+abs(a[1]-b[1]);r0,c0=targets[0];options=[]
    for r in range(max(0,r0-6),min(device.rows,r0+7)):
        for c in range(max(0,c0-6),min(device.cols,c0+7)):
            if device.grid[r][c]!=17 or (r,c) in occupied:continue
            score=2*max(distance((r,c),p) for p in targets)+sum(distance((r,c),p) for p in anchors)/max(1,len(anchors))
            options.append((score,r,c))
    legalization=None
    if legalize:
        from ownpr_macro_legalize import clear_site
        # An existing leaf identifies the legalizer's logical context. Its
        # complete old macro remains protected/excluded; only the new CFU is
        # cleared. Incoming drivers and selected receivers are kept fixed.
        def cost(rr,cc):
            return 8*max(distance((rr,cc),p) for p in targets)+2*sum(distance((rr,cc),p) for p in anchors)/max(1,len(anchors))
        protected=set(members)|{n for n,p in sinks}|{drivers[b] for b in inputs if b in drivers}|set(protected_cells)
        placed,(r,c),legalization=clear_site(design,placed,next(iter(spec['leaves'])),device,cost,protected,candidates=12)
    else:
        if not options:raise ValueError('No free calibrated CFU near macro copy sinks')
        _,r,c=min(options)
    loads={(n,p) for n,cell in cells.items() for p,bits in cell['connections'].items() if cell['port_directions'][p]=='input' and old_bit in bits}
    if set(map(tuple,sinks))==loads:
        # Copying every load would leave a dead original macro. The same
        # candidate location can instead be tested with zero logic growth.
        from ownpr_wide_macro_move import relocate
        layout,movement=relocate(design,placed,device,source,r,c);layout.pop('weighted_internal_manhattan',None)
        return copy.deepcopy(design),layout,dict(kind='whole_wide_macro_relocation',source=source,replica=source,old_bit=old_bit,new_bit=old_bit,sinks=sinks,
            site=[r,c,macro['root_slot']],lut_growth=0,mux_growth=0,clocks_changed=any(placed['clusters'][n].get('ff') for n in spec['leaves']) or bool(legalization and legalization['clock_replan_required']),state_cells_changed=False,fixed_io_changed=False,
            movement=movement,legalization=legalization,reason='All original loads were selected; relocate rather than leave an unused original macro')
    new=copy.deepcopy(design);layout=copy.deepcopy(placed);module=new['modules']['top'];names=module['cells'];ordered=sorted(members);index=0
    while any(f'ng_ownpr_macro_copy_{index}_{i}' in names or f'ng_ownpr_macro_copy_{index}_{i}_out' in module.get('netnames',{}) for i in range(len(ordered))):index+=1
    copies={n:f'ng_ownpr_macro_copy_{index}_{i}' for i,n in enumerate(ordered)}
    bits=[b for row in cells.values() for bs in row['connections'].values() for b in bs]
    bits.extend(b for row in module.get('netnames',{}).values() for b in row['bits']);bits.extend(b for row in module['ports'].values() for b in row['bits'])
    first_bit=max(b for b in bits if isinstance(b,int))+1;bit_map={b:first_bit+i for i,b in enumerate(sorted(internal))}
    for name in ordered:
        copy_name=copies[name];clone=copy.deepcopy(cells[name]);clone['connections']={p:[bit_map.get(b,b) for b in bs] for p,bs in clone['connections'].items()};names[copy_name]=clone
        output=clone['connections']['F' if name in spec['leaves'] else 'O'][0]
        module.setdefault('netnames',{})[copy_name+'_out']=dict(hide_name=1,bits=[output],attributes={})
        if name in spec['leaves']:layout['clusters'][copy_name]=dict(row=r,column=c,slot=spec['leaves'][name],lut=copy_name,ff=None)
        else:layout['mux_clusters'][copy_name]=dict(**spec['muxes'][name],row=r,column=c)
    for name,pin in sinks:names[name]['connections'][pin]=[bit_map[old_bit]]
    layout['wide_macros'].append(dict(root=copies[source],row=r,column=c,root_slot=macro['root_slot']))
    layout.pop('weighted_internal_manhattan',None);layout.update(physical_legality_complete=False,timing_signoff=False);validate(new,layout,device)
    if any(n in placed.get('arithmetic_clusters',{}) for n,p in sinks):
        from ownpr_carry_macros import validate as validate_carry
        validate_carry(new,layout,device)
    return new,layout,dict(kind='whole_wide_macro',source=source,replica=copies[source],members=copies,bit_map={str(k):v for k,v in bit_map.items()},old_bit=old_bit,new_bit=bit_map[old_bit],
        sinks=sinks,site=[r,c,macro['root_slot']],lut_growth=len(spec['leaves']),mux_growth=len(spec['muxes']),
        clocks_changed=bool(legalization and legalization['clock_replan_required']),state_cells_changed=False,fixed_io_changed=False,legalization=legalization,
        ranking='Legalized sink/input distance with displaced-cluster penalty' if legalize else 'Free-CFU sink distance with external-input distance penalty; full routed STA required')
