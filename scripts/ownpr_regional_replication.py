"""Replicate an ordinary control cone and synchronous state frontier near sinks.

Copies preserve cycle count. State aliases require an inductive CEC proof;
geometric proximity is only a placement heuristic, never timing signoff.
"""
import argparse,copy,json,time,shutil
from pathlib import Path
from ownpr_device import Device,ROOT
from ownpr_image_storage import load_json,sha
from ownpr_critical_chain_move import exclusive_tiles,locations
from native_boundary_cec import initial_values,initial

LUTS={'LUT1','LUT2','LUT3','LUT4'}

def replicate(design,placed,device,source,sinks,depth=3,max_registers=12,allowed_registers=None,cluster_region=False,protected_cells=()):
    if not 1<=depth<=6:raise ValueError('Cone depth must be 1..6')
    module=design['modules']['top'];cells=module['cells'];clusters=placed['clusters']
    paired={p['ff']:n for n,p in clusters.items() if p.get('ff') and not p.get('wide_ff_root') and cells[n]['type'] in LUTS}
    state_root=source in paired and cells[source]['type'] in ('DFFRE','DFFSE')
    if allowed_registers is not None:
        allowed_registers=set(allowed_registers)
        if not allowed_registers or any(n not in cells or cells[n]['type'] not in ('DFFRE','DFFSE') for n in allowed_registers):raise ValueError('Invalid selected state frontier')
    if not state_root and (source not in clusters or cells[source]['type'] not in LUTS):raise ValueError('Root must be an ordinary LUT or supported paired synchronous register')
    if not sinks or len(set(map(tuple,sinks)))!=len(sinks):raise ValueError('Empty or duplicate sink set')
    bit=cells[source]['connections']['Q' if state_root else 'F'][0];points=locations(placed);anchors=[]
    for name,pin in sinks:
        ordinary=name in clusters and cells[name]['type'] in LUTS and pin in ('I0','I1','I2','I3')
        arithmetic=name in placed.get('arithmetic_clusters',{}) and cells[name]['type']=='ALU' and pin in ('I0','I1','I3')
        selector=name in placed.get('mux_clusters',{}) and cells[name]['type'] in ('MUX2_LUT5','MUX2_LUT6','MUX2_LUT7') and pin=='S0'
        if not (ordinary or arithmetic or selector) or cells[name]['connections'].get(pin)!=[bit]:
            raise ValueError('Only ordinary LUT, ALU data or wide selector input sinks supported')
        anchors.append(points[name])
    drivers={b:n for n,c in cells.items() for p,bs in c['connections'].items() if c['port_directions'][p]=='output' for b in bs if isinstance(b,int)}
    excluded=exclusive_tiles(placed)
    wide_inputs={b for c in cells.values() if c['type'].startswith('MUX2_LUT') for p in ('I0','I1') for b in c['connections'][p]}
    cone=set();frontier=set();active=set()
    def walk(name,level):
        if name in active:raise ValueError('Combinational cycle in cone')
        c=cells[name]
        if c['type'] in ('DFFRE','DFFSE'):
            if name in paired and (allowed_registers is None or name in allowed_registers):frontier.add(name)
            return
        if c['type'] not in LUTS or name not in clusters or level>=depth:return
        if c['connections']['F'][0] in wide_inputs:raise ValueError('Dedicated wide inputs cannot be copied as ordinary LUTs')
        if name in cone:return
        active.add(name);cone.add(name)
        for p,bs in c['connections'].items():
            if c['port_directions'][p]=='input':
                for b in bs:
                    if b in drivers:walk(drivers[b],level+1)
        active.remove(name)
    walk(source,0)
    if not frontier or len(frontier)>max_registers:raise ValueError('Register frontier is empty or exceeds budget')
    if any(n in cone for n,p in sinks):raise ValueError('Sink overlaps replicated cone')
    copied=cone|{paired[n] for n in frontier}|frontier
    new=copy.deepcopy(design);layout=copy.deepcopy(placed);out=new['modules']['top'];names=out['cells'];netnames=out.setdefault('netnames',{})
    prefix='ng_ownpr_region_';index=0
    while any(n.startswith(f'{prefix}{index}_') for n in names) or any(n.startswith(f'{prefix}{index}_') for n in netnames):index+=1
    mapping={n:f'{prefix}{index}_{i}' for i,n in enumerate(sorted(copied))}
    bits=[b for c in cells.values() for bs in c['connections'].values() for b in bs]
    bits.extend(b for r in module['ports'].values() for b in r['bits']);bits.extend(b for r in module.get('netnames',{}).values() for b in r['bits'])
    next_bit=max(b for b in bits if isinstance(b,int))+1;aliases={}
    for name in sorted(copied):
        c=cells[name]
        for pin,bs in c['connections'].items():
            if c['port_directions'][pin]=='output':
                if len(bs)!=1 or not isinstance(bs[0],int):raise ValueError('Non-scalar cone output')
                aliases[bs[0]]=next_bit;next_bit+=1
    init=initial_values(module)
    for name in sorted(copied):
        c=copy.deepcopy(cells[name]);clone=mapping[name]
        # Only the cone and paired D logic are remapped. CLK is always identical;
        # CE/SR remain on their original nets to avoid changing control semantics.
        for pin,bs in c['connections'].items():
            if name in frontier and pin in ('CLK','CE','RESET','SET'):continue
            c['connections'][pin]=[aliases.get(b,b) for b in bs]
        names[clone]=c
        output='Q' if name in frontier else 'F';attrs={}
        if name in frontier:
            value=initial(init,cells[name],cells[name]['connections']['Q'][0])
            if value not in ('0','1'):raise ValueError('Unspecified initial register state')
            attrs['init']=value
        netnames[clone+'_out']=dict(hide_name=1,bits=c['connections'][output],attributes=attrs)
    for name,pin in sinks:names[name]['connections'][pin]=[aliases[bit]]
    occupied={(p['row'],p['column'],p['slot']) for p in clusters.values()}
    controls={(p['row'],p['column'],p['slot']//2) for p in clusters.values() if p.get('ff')}
    distance=lambda a,b:abs(a[0]-b[0])+abs(a[1]-b[1])
    center=(round(sum(p[0] for p in anchors)/len(anchors)),round(sum(p[1] for p in anchors)/len(anchors)))
    units={paired[ff]:ff for ff in frontier}
    for name in cone:units.setdefault(name,None)
    legalization=None;cluster_site=None;forced_slot=None
    if cluster_region:
        if len(units)>8 or len(frontier)>3:raise ValueError('Regional cone exceeds one-CFU packing capacity')
        from ownpr_macro_legalize import clear_site
        external_points=[]
        for name in copied:
            for pin,bs in cells[name]['connections'].items():
                if pin=='CLK' or cells[name]['port_directions'][pin]!='input':continue
                external_points.extend(points[drivers[b]] for b in bs if drivers.get(b) in points and drivers[b] not in copied)
        def regional_cost(r,c):
            if (r,c)==points[source]:return float('inf')
            return 8*max(distance((r,c),p) for p in anchors)+sum(distance((r,c),p) for p in external_points)/max(1,len(external_points))
        protected={n for n,pin in sinks}|frontier|set(protected_cells)
        if state_root:
            from ownpr_macro_legalize import clear_register_slot
            layout,target,legalization=clear_register_slot(design,layout,device,regional_cost,protected)
            cluster_site=target[:2];forced_slot=target[2]
        else:
            layout,cluster_site,legalization=clear_site(design,layout,source,device,regional_cost,protected,candidates=12)
        occupied={(p['row'],p['column'],p['slot']) for p in layout['clusters'].values()}
        controls={(p['row'],p['column'],p['slot']//2) for p in layout['clusters'].values() if p.get('ff')}
    placements=[]
    for lut,ff in sorted(units.items(),key=lambda x:(x[1] is None,x[0])):
        external=[]
        for member in [lut]+([ff] if ff else []):
            for pin,bs in cells[member]['connections'].items():
                if pin=='CLK' or cells[member]['port_directions'][pin]!='input':continue
                for b in bs:
                    driver=drivers.get(b)
                    if driver in points and driver not in copied:external.append(points[driver])
        options=[]
        for r in ([cluster_site[0]] if cluster_site is not None else range(max(0,center[0]-5),min(device.rows,center[0]+6))):
            for c in ([cluster_site[1]] if cluster_site is not None else range(max(0,center[1]-5),min(device.cols,center[1]+6))):
                if device.grid[r][c]!=17 or (r,c) in excluded:continue
                for s in ([forced_slot] if forced_slot is not None else (0,2,4) if ff else range(8)):
                    if (r,c,s) in occupied or ff and (r,c,s//2) in controls:continue
                    score=4*max(distance((r,c),p) for p in anchors)+sum(distance((r,c),p) for p in external)/max(1,len(external))
                    options.append((score,r,c,s))
        if not options:raise ValueError('No nearby legal regional slot')
        score,r,c,s=min(options);occupied.add((r,c,s))
        if ff:controls.add((r,c,s//2))
        layout['clusters'][mapping[lut]]=dict(lut=mapping[lut],ff=mapping[ff] if ff else None,row=r,column=c,slot=s)
        placements.append(dict(original=lut,replica=mapping[lut],ff=mapping[ff] if ff else None,site=[r,c,s],score=score))
    from ownpr_ff_configuration import compile_initial
    from ownpr_mux_macros import validate as wide_validate
    from ownpr_carry_macros import validate as carry_validate
    compile_initial(new,layout);wide_validate(new,layout,device);carry_validate(new,layout,device)
    if layout.get('hard_clusters'):
        from ownpr_hard_architecture import HardArchitecture
        HardArchitecture().validate(new,layout,device)
    layout.pop('weighted_internal_manhattan',None);layout.update(timing_signoff=False,physical_legality_complete=False)
    return new,layout,dict(source=source,source_kind='state' if state_root else 'ordinary_logic',sinks=sinks,cone=sorted(cone),register_replicas={mapping[n]:n for n in sorted(frontier)},
        lut_growth=len(copied)-len(frontier),register_growth=len(frontier),placements=placements,clock_replan_required=True,
        fixed_io_changed=False,interface_cycles_changed=False,selected_state_frontier=sorted(allowed_registers) if allowed_registers is not None else None,
        clustered_region=cluster_region,cluster_site=cluster_site,legalization=legalization,
        ranking='Bounded regional proximity; all incoming D/control paths require routed STA')

def main():
    p=argparse.ArgumentParser(description=__doc__)
    for key in ('base','out'):p.add_argument('--'+key,type=Path,required=True)
    p.add_argument('--source',required=True);p.add_argument('--sink',action='append',required=True);p.add_argument('--depth',type=int,default=3)
    a=p.parse_args();start=time.monotonic()
    from workspace_budget import reserve
    reserve(ROOT,100_000_000)
    design=load_json(a.base/'packed.json');placed=load_json(a.base/'placement.json')
    new,layout,change=replicate(design,placed,Device(),a.source,[tuple(s.rsplit('/',1)) for s in a.sink],a.depth)
    a.out.mkdir(parents=True,exist_ok=False)
    sources=[Path(__file__),ROOT/'scripts/ownpr_register_equivalence.py',ROOT/'scripts/native_boundary_cec.py']
    for src in sources:shutil.copyfile(src,a.out/(src.name+'.txt'))
    from ownpr_register_equivalence import prove_replicas
    proof=prove_replicas(design,new,change['register_replicas'],a.out/'proof',180)
    for name,value in [('packed.json',new),('placement.json',layout),('change.json',change),('proof.json',proof)]:
        (a.out/name).write_text(json.dumps(value,indent=2))
    meta=load_json(a.base/'manifest.json');meta.update(status='placed',netlist_sha256=sha(a.out/'packed.json'),placement_sha256=sha(a.out/'placement.json'),
        source_netlist_sha256=sha(a.base/'packed.json'),source_placement_sha256=sha(a.base/'placement.json'),cells=len(new['modules']['top']['cells']),
        ordinary_clusters=len(layout['clusters']),register_replication=change,proof=proof,source_sha256={src.name:sha(a.out/(src.name+'.txt')) for src in sources},
        wall_seconds=time.monotonic()-start,timing_signoff=False,physical_legality_complete=False)
    (a.out/'manifest.json').write_text(json.dumps(meta,indent=2));print(json.dumps(change,indent=2))

if __name__=='__main__':main()
