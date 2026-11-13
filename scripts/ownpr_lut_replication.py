"""CEC-proved LUT copies near critical sinks, with fixed state/hard/I/O sites."""
import argparse,copy,json,sys,time
from pathlib import Path
from ownpr_device import Device,ROOT
from ownpr_image_storage import load_json,sha


def replicate(design,placed,device,source,sinks):
    cells=design['modules']['top']['cells'];original=cells[source]
    if original['type'] not in ('LUT1','LUT2','LUT3','LUT4') or source not in placed['clusters']:raise ValueError('Only ordinary LUT drivers may be replicated')
    wide_members={n for n,c in cells.items() if c['type'].startswith('MUX2_LUT')}
    wide_inputs={b for n in wide_members for p in ('I0','I1') for b in cells[n]['connections'][p]}
    old_bit=original['connections']['F'][0]
    if old_bit in wide_inputs:raise ValueError('Do not split dedicated wide-macro inputs')
    if not sinks or len(sinks)!=len(set(map(tuple,sinks))):raise ValueError('Missing/duplicate replica sink')
    from ownpr_ff_configuration import FF_CONTROLS
    ff_sites={row['ff']:row for row in placed['clusters'].values() if row.get('ff')};sink_points=[];enable_sinks=[]
    for name,pin in sinks:
        ordinary=name in placed['clusters'] and cells[name]['type'] in ('LUT1','LUT2','LUT3','LUT4') and pin in ('I0','I1','I2','I3')
        selector=name in placed.get('mux_clusters',{}) and cells[name]['type'] in ('MUX2_LUT5','MUX2_LUT6','MUX2_LUT7') and pin=='S0'
        enable=name in ff_sites and cells[name]['type'] in FF_CONTROLS and pin=='CE'
        if not (ordinary or selector or enable) or cells[name]['connections'].get(pin)!=[old_bit]:raise ValueError('Replica sink does not match a supported ordinary logical edge, mux selector or synchronous FF enable')
        point=ff_sites[name] if enable else placed['clusters'][name] if ordinary else placed['mux_clusters'][name]
        if enable:enable_sinks.append(name)
        sink_points.append((point['row'],point['column']))
    # No clocks, asynchronous controls, FF data pairing or hard-block pins are
    # changed. CE rewiring requires the caller's full boundary CEC and STA.
    occupied={(p['row'],p['column'],p['slot']) for p in placed['clusters'].values()}
    excluded={(p['row'],p['column']) for category in ('arithmetic_clusters','mux_clusters') for p in placed.get(category,{}).values()}
    for p in placed.get('hard_clusters',{}).values():excluded.update((p['row'],p['column']+i) for i in range(3))
    drivers={b:n for n,c in cells.items() for pin,bs in c['connections'].items() if c['port_directions'][pin]=='output' for b in bs}
    locations={n:(p['row'],p['column']) for n,p in placed['clusters'].items()}
    locations.update({name:(p['row'],p['column']) for name,p in ff_sites.items()})
    input_points=[locations[drivers[b]] for pin,bs in original['connections'].items() if original['port_directions'][pin]=='input' for b in bs if drivers.get(b) in locations]
    distance=lambda a,b:abs(a[0]-b[0])+abs(a[1]-b[1]);r0,c0=sink_points[0];options=[]
    for r in range(max(0,r0-4),min(device.rows,r0+5)):
        for c in range(max(0,c0-4),min(device.cols,c0+5)):
            if device.grid[r][c]!=17 or (r,c) in excluded:continue
            for slot in range(8):
                if (r,c,slot) in occupied:continue
                score=2*max(distance((r,c),p) for p in sink_points)+sum(distance((r,c),p) for p in input_points)/max(1,len(input_points))
                options.append((score,r,c,slot))
    if not options:raise ValueError('No legal nearby replica slot')
    _,r,c,slot=min(options);new=copy.deepcopy(design);layout=copy.deepcopy(placed);module=new['modules']['top'];names=module['cells']
    index=0
    while f'ng_ownpr_replica_{index}' in names or f'ng_ownpr_replica_{index}_out' in module.get('netnames',{}):index+=1
    name=f'ng_ownpr_replica_{index}'
    bits=[b for row in cells.values() for bs in row['connections'].values() for b in bs]
    bits.extend(b for row in module.get('netnames',{}).values() for b in row['bits']);bits.extend(b for row in module['ports'].values() for b in row['bits'])
    bit=1+max(b for b in bits if isinstance(b,int))
    clone=copy.deepcopy(original);clone['connections']['F']=[bit];names[name]=clone
    for target,pin in sinks:names[target]['connections'][pin]=[bit]
    module.setdefault('netnames',{})[name+'_out']=dict(hide_name=1,bits=[bit],attributes={})
    layout['clusters'][name]=dict(row=r,column=c,slot=slot,lut=name,ff=None)
    layout.pop('weighted_internal_manhattan',None)
    layout.update(timing_signoff=False,physical_legality_complete=False)
    return new,layout,dict(source=source,replica=name,old_bit=old_bit,new_bit=bit,sinks=sinks,site=[r,c,slot],lut_growth=1,
        clocks_changed=False,state_cells_changed=bool(enable_sinks),synchronous_enables_rewired=enable_sinks,state_storage_changed=False,fixed_io_changed=False,ranking='Sink proximity with input-wire distance penalty; full boundary CEC and routed STA are mandatory')


def main():
    p=argparse.ArgumentParser();p.add_argument('--base',type=Path,required=True);p.add_argument('--routes',type=Path,required=True);p.add_argument('--sta',type=Path,required=True);p.add_argument('--out',type=Path,required=True);p.add_argument('--cst',type=Path,required=True);p.add_argument('--sdc',type=Path,required=True)
    p.add_argument('--driver-kind',choices=('ordinary','wide','any'),default='ordinary');p.add_argument('--sink-kind',choices=('any','enable'),default='any');a=p.parse_args()
    from workspace_budget import reserve
    reserve(ROOT,100_000_000);start=time.monotonic();a.out.mkdir(exist_ok=False)
    from audit_ownpr_native_export import audit
    audit(a.sta,a.base)
    sys.path.insert(0,str(ROOT/'experiments'))
    from analyze_ownpr_critical_wires import analyze
    ranking=analyze(a.sta,a.base,a.routes,32);design=load_json(a.base/'packed.json');placed=load_json(a.base/'placement.json');device=Device();selected=None;rejections=[]
    for candidate in ranking['ranked_nets']:
        primitive=candidate['driver']['primitive'];wide=primitive in ('MUX2_LUT5','MUX2_LUT6','MUX2_LUT7')
        if primitive not in ('LUT1','LUT2','LUT3','LUT4','MUX2_LUT5','MUX2_LUT6','MUX2_LUT7') or candidate['fanout']<2:continue
        if a.driver_kind=='wide' and not wide or a.driver_kind=='ordinary' and wide:continue
        sinks=[tuple(endpoint.rsplit('/',1)) for endpoint in candidate['endpoints']]
        if a.sink_kind=='enable':
            from ownpr_ff_configuration import FF_CONTROLS
            sinks=[(name,pin) for name,pin in sinks if design['modules']['top']['cells'][name]['type'] in FF_CONTROLS and pin=='CE']
            if not sinks:continue
        implementation=replicate
        if wide:
            from ownpr_macro_replication import replicate as macro_replicate
            implementation=macro_replicate
        try:new,layout,change=implementation(design,placed,device,candidate['driver']['instance'],sinks)
        except ValueError as e:rejections.append(dict(net=candidate['net'],reason=str(e)));continue
        selected=candidate;break
    if selected is None:raise ValueError('No supported critical LUT driver')
    from native_boundary_cec import prove
    proof=prove(design,new,a.out/'cec',120)
    from ownpr_validate import placement
    from ownpr_place import constraints
    external=constraints(new['modules']['top'],a.cst.read_text(),a.sdc.read_text());runtime=ROOT/'results/native_route_runtime_007'
    logic=placement(new,layout,load_json(runtime/'native_grid_types.json')['grid'],load_json(runtime/'native_iob_ports.json'),device.pin_site_name,external)
    from ownpr_carry_macros import validate as carry_validate
    from ownpr_mux_macros import validate as mux_validate
    from ownpr_hard_architecture import HardArchitecture
    carry=carry_validate(new,layout,device);mux=mux_validate(new,layout,device);hard=HardArchitecture().validate(new,layout,device) if layout.get('hard_clusters') else None
    from ownpr_ff_configuration import compile_initial
    compile_initial(new,layout)
    for name,value in [('packed.json',new),('placement.json',layout),('replication.json',change),('logic_placement_audit.json',logic),('carry_placement_audit.json',carry)]:
        (a.out/name).write_text(json.dumps(value,indent=2))
    meta=load_json(a.base/'manifest.json');meta.update(status='placed',netlist_sha256=sha(a.out/'packed.json'),placement_sha256=sha(a.out/'placement.json'),proof=proof,
        cells=len(new['modules']['top']['cells']),ordinary_clusters=len(layout['clusters']),
        source_placement_sha256=sha(a.base/'placement.json'),source_netlist_sha256=sha(a.base/'packed.json'),source_sta_sha256=sha(a.sta/'handoff_audit.json'),
        replication=change,replication_sink_kind=a.sink_kind,hard_resources=hard,wide_mux_resources=mux,wall_seconds=time.monotonic()-start,timing_signoff=False,physical_legality_complete=False)
    (a.out/'manifest.json').write_text(json.dumps(meta,indent=2));(a.out/'ranking.json').write_text(json.dumps(dict(selected=selected,rejections=rejections,analysis=ranking),indent=2));print(json.dumps(change,indent=2))


if __name__=='__main__':main()
