"""Independent initial packer, weighted analytical placement and legalization.

Native DLL/FSE data only. Initial supported slice: LUT1..4, positive-edge
DFFRE, fixed scalar IBUF/OBUF and constant inactive GSR. Unsupported resources
are rejected. This is not full-device or timing-signoff completion.
"""
import argparse,copy,json,re,time,hashlib
from collections import defaultdict,Counter
from pathlib import Path
import numpy as np
from ownpr_device import Device,ROOT
from native_boundary_cec import prove
from ownpr_ff_configuration import FF_CONTROLS

def sha(p):
    from ownpr_image_storage import sha as artifact_sha
    return artifact_sha(p)

def constraints(module,cst,sdc):
    if re.search(r'\bINS_LOC\b',cst):raise ValueError('Internal fixed placement constraints are not supported yet')
    pins=dict(re.findall(r'IO_LOC\s+"([^"]+)"\s+(\w+)\s*;',cst))
    properties=dict(re.findall(r'IO_PORT\s+"([^"]+)"\s+([^;]+);',cst))
    clock_directives=re.findall(r'^\s*CLOCK_LOC\s+"([^"]+)"\s+([^;\n]+);',cst,re.M)
    if len(clock_directives)!=len(re.findall(r'^\s*CLOCK_LOC\b',cst,re.M)):raise ValueError('Malformed clock routing constraint')
    clock_routing=[];seen_clock_nets=set()
    for name,policy in clock_directives:
        if policy.strip()!='LOCAL_CLOCK':raise ValueError('Unsupported explicit clock routing policy')
        bits=module.get('netnames',{}).get(name,{}).get('bits',[])
        if len(bits)!=1 or name in seen_clock_nets:raise ValueError('Clock routing target is missing, nonscalar or duplicated')
        seen_clock_nets.add(name);clock_routing.append(dict(net=name,bit=bits[0],policy='LOCAL_CLOCK'))
    ports={}
    for name,p in module['ports'].items():
        for i,b in enumerate(p['bits']):ports[b]=name if len(p['bits'])==1 else f'{name}[{i+p.get("offset",0)}]'
    if not set(ports.values())<=pins.keys():raise ValueError('Every port must have a frozen package pin')
    if len(set(pins.values()))!=len(pins):raise ValueError('Two ports assigned to one pin')
    clocks=re.findall(r'create_clock\s+-name\s+(\w+)\s+-period\s+([\d.]+)\s+\[get_ports\s+\{([^}]+)\}\]',sdc)
    if len(clocks)!=1:raise ValueError('Initial own placement supports exactly one clock')
    if any(x in sdc for x in ('set_false_path','set_multicycle_path','create_generated_clock')):raise ValueError('Unsupported timing exception/generated clock')
    clock,period,clock_port=clocks[0]
    delays=defaultdict(list)
    for kind,mode,value,ref,names in re.findall(r'set_(input|output)_delay\s+-(max|min)\s+([\d.]+)\s+-clock\s+(\w+)\s+\[get_ports\s+\{([^}]+)\}\]',sdc):
        if ref!=clock or float(value)!=0:raise ValueError('Initial contract requires explicit zero I/O delays')
        delays[kind,mode].extend(names.split())
    for _,p in module['ports'].items():
        for b in p['bits']:
            name=ports[b]
            if name==clock_port:continue
            direction={'input':'input','output':'output'}.get(p['direction'])
            if not direction:raise ValueError('Bidirectional I/O unsupported in initial packer')
            for mode in ('max','min'):
                if not any(re.fullmatch(re.escape(pattern).replace(r'\[\*\]',r'\[\d+\]'),name) for pattern in delays[direction,mode]):raise ValueError('Missing I/O timing bound for '+name)
    return dict(pins=pins,properties=properties,ports=ports,period_ns=float(period),clock_port=clock_port,clock_routing=clock_routing)

def pack(before,top,allow_alu=False,allow_wide=False,allow_hard=False):
    design=copy.deepcopy(before);m=design['modules'][top];cells=m['cells'];allowed={'IBUF','OBUF','GND','VCC','GSR','LUT1','LUT2','LUT3','LUT4'}|set(FF_CONTROLS)
    if allow_alu:allowed.add('ALU')
    if allow_wide:allowed.update(('MUX2_LUT5','MUX2_LUT6','MUX2_LUT7'))
    if allow_hard:
        from ownpr_hard_architecture import HardArchitecture,HARD_TYPES
        allowed.update(HARD_TYPES);HardArchitecture().contract(design,top)
    unsupported=Counter(c['type'] for c in cells.values() if c['type'] not in allowed)
    if unsupported:raise ValueError('Unsupported physical primitives: '+str(dict(unsupported)))
    dynamic_heads=[]
    if allow_hard:
        from ownpr_carry_outputs import materialize_heads
        design,dynamic_heads=materialize_heads(design,top);m=design['modules'][top];cells=m['cells']
    constants={'0':0,'1':1}
    for c in cells.values():
        if c['type'] in ('VCC','GND'):constants[c['connections']['V' if c['type']=='VCC' else 'G'][0]]=int(c['type']=='VCC')
    for c in cells.values():
        if c['type']=='GSR' and constants.get(c['connections']['GSRI'][0])!=1:raise ValueError('User-active GSR not implemented')
    if allow_wide:
        from ownpr_lut_lifting import lift
        drivers0={b:n for n,c in cells.items() for p,bs in c['connections'].items() if c['port_directions'][p]=='output' for b in bs}
        ground=next(c['connections']['G'][0] for c in cells.values() if c['type']=='GND')
        leaves={drivers0[c['connections'][p][0]] for c in cells.values() if c['type']=='MUX2_LUT5' for p in ('I0','I1')}
        for n in leaves:
            if cells[n]['type'] not in ('LUT1','LUT2','LUT3','LUT4'):raise ValueError('Invalid wide LUT leaf')
            if cells[n]['type']!='LUT4':cells[n]=lift(cells[n],ground)
    drivers={}
    for name,c in cells.items():
        for p,bs in c['connections'].items():
            if c['port_directions'][p]=='output':
                for b in bs:
                    if b in drivers and b not in constants:raise ValueError('Multiple signal drivers')
                    drivers[b]=(name,p)
    maximum=max(b for c in cells.values() for bs in c['connections'].values() for b in bs if isinstance(b,int))
    input_pads=[]
    if allow_hard:
        # Preserve genuinely unused top-level inputs as physical pads. No
        # functional consumer or interface cycle is introduced.
        consumed={b for c in cells.values() for p,bs in c['connections'].items() if c['port_directions'][p]=='input' for b in bs}
        for port,spec in m['ports'].items():
            if spec['direction']!='input':continue
            for bit in spec['bits']:
                if bit in consumed:continue
                name='ng_ownpr_unused_input_'+str(len(input_pads))
                if name in cells:raise ValueError('Generated unused pad name collision')
                maximum+=1;cells[name]=dict(type='IBUF',hide_name=0,parameters={},attributes={},port_directions={'I':'input','O':'output'},connections={'I':[bit],'O':[maximum]})
                m.setdefault('netnames',{})[name+'_net']=dict(hide_name=0,bits=[maximum],attributes={});input_pads.append(dict(cell=name,port=port,bit=bit))
    groups={n:dict(lut=n,ff=None) for n,c in cells.items() if c['type'].startswith('LUT')};added=[]
    for name,c in list(cells.items()):
        if c['type'] not in FF_CONTROLS:continue
        if any(len(c['connections'].get(p,[]))!=1 for p in ('CLK','CE',FF_CONTROLS[c['type']],'D','Q')):
            raise ValueError('Invalid FF pin shape')
        # One FF per CLS avoids sharing its CLK/CE/synchronous-reset controls
        # with an independently controlled register; logical controls retained.
        d=c['connections']['D'][0];driver=drivers.get(d,(None,None))[0]
        if driver not in groups or groups[driver]['ff'] is not None:
            new=f'ng_ownpr_pass_{len(added)}'
            if new in cells:raise ValueError('Generated pass-through name collision')
            maximum+=1;cells[new]=dict(type='LUT1',hide_name=0,parameters={'INIT':'10'},attributes={},port_directions={'I0':'input','F':'output'},connections={'I0':[d],'F':[maximum]})
            m.setdefault('netnames',{})[new+'_net']=dict(hide_name=0,bits=[maximum],attributes={})
            c['connections']['D']=[maximum];groups[new]=dict(lut=new,ff=None);driver=new;added.append(new)
        groups[driver]['ff']=name
    return design,groups,constants,dict(passthrough_luts=added,state_cells_changed=False,pipeline_stages_added=0,materialized_unused_inputs=input_pads,dynamic_carry_heads=dynamic_heads)

def place(design,top,groups,constants,contract,device,runtime,carry=None,clock_sites=None,critical_endpoints=None,wide=None,hard=None,ff_sites=None):
    rm=json.loads((runtime/'manifest.json').read_text())
    if rm['open_chip_database_used'] or rm['status']!='extracted':raise ValueError('Native architecture provenance required')
    for key,file in [('grid_sha256','native_grid_types.json'),('iob_ports_sha256','native_iob_ports.json')]:
        if sha(runtime/file)!=rm[key]:raise ValueError('Native site capture changed')
    native=json.loads((runtime/'native_grid_types.json').read_text())['grid']
    io=json.loads((runtime/'native_iob_ports.json').read_text());iob={(r['native_row'],r['native_column'],r['slot']):r for r in io}
    cells=design['modules'][top]['cells'];fixed={};pin_bindings={};unit={n:g for g,row in groups.items() for n in (row['lut'],row['ff']) if n}
    macros={}
    from ownpr_hard_architecture import HARD_TYPES
    hard_names={n for n,c in cells.items() if c['type'] in HARD_TYPES}
    if hard_names!=set(hard or {}):raise ValueError('Hard cells require an explicit placement contract')
    for name in hard_names:unit[name]=name
    wides={spec['root']:spec for spec in (wide or [])};all_groups=groups
    if wides:
        leaves={n for spec in wides.values() for n in spec['leaves']}
        if any(groups[n]['ff'] for n in leaves):raise ValueError('Wide leaf has a paired FF')
        groups={n:g for n,g in groups.items() if n not in leaves}
        for root,spec in wides.items():
            for n in set(spec['leaves'])|set(spec['muxes']):unit[n]=root
    if carry:
        for i,chain in enumerate(carry['chains']):
            key='__own_carry_macro_'+str(i)
            if key in groups or key in cells:raise ValueError('Carry macro identifier collision')
            macros[key]=chain
            for name in chain['cells']:unit[name]=key
    anchored={};anchor_tiles=set();extended_anchor_tiles=set()
    if ff_sites is not None:
        if hard_names:raise ValueError('Fixed FF sites with movable hard blocks are not implemented')
        lut_cal=json.loads((ROOT/'results/native_device_lut_004/lut_encoding_audit.json').read_text());cls_cal=json.loads((ROOT/'results/native_cls_modes_002/cls_mode_sweep.json').read_text())
        if any(c['source_fse_sha256']!=device.fse.sha256 for c in (lut_cal,cls_cal)) or not all(r['match'] for r in lut_cal['checks']):raise ValueError('FF anchor calibration mismatch')
        covered={(r['tile_type'],r['group']) for r in cls_cal['groups']};allowed={t for t in lut_cal['tile_types'] if all((t,g) in covered for g in range(4))}
        expected={g['ff'] for g in groups.values() if g.get('ff')}
        if set(ff_sites)!=expected:raise ValueError('Fixed FF anchors must cover every FF exactly')
        used=set();controls=set()
        for name,group in groups.items():
            if not group.get('ff'):continue
            point=tuple(ff_sites[group['ff']]);r,c,slot=point
            if len(point)!=3 or any(type(x) is not int for x in point) or not(0<=r<device.rows and 0<=c<device.cols) or device.grid[r][c] not in allowed or native[r+1][c+1]!=5 or slot not in range(8):raise ValueError('Uncalibrated fixed FF site')
            if slot not in (0,2,4):
                from ownpr_ff_configuration import single_ff_any_slot_calibration
                if device.grid[r][c]!=17 or sum(tuple(p[:2])==(r,c) for p in ff_sites.values())!=1:raise ValueError('Extended FF anchor requires a single FF in a type17 CFU')
                single_ff_any_slot_calibration()
                extended_anchor_tiles.add((r,c))
            if clock_sites is not None and (r,c) not in clock_sites:raise ValueError('Fixed FF anchor violates the clock placement budget')
            if point in used or (r,c,slot//2) in controls:raise ValueError('Overlapping fixed FF sites')
            used.add(point);controls.add((r,c,slot//2));anchored[name]=point;anchor_tiles.add((r,c));fixed[name]=np.array([r,c],dtype=float)
    for name,c in cells.items():
        if c['type'] not in ('IBUF','OBUF'):continue
        padpin='I' if c['type']=='IBUF' else 'O';port=contract['ports'][c['connections'][padpin][0]];pin=contract['pins'][port]
        loc=device.pin_site_name(pin);side,index,letter=re.fullmatch(r'IO([TBLR])(\d+)([AB])',loc).groups();index=int(index)
        r,col={'T':(0,index),'B':(rm['rows']-1,index),'L':(index,0),'R':(index,rm['columns']-1)}[side]
        rec=iob.get((r,col,ord(letter)-65))
        if not rec or len(rec['buffers'])!=1:raise ValueError('Unverified I/O buffer binding '+loc)
        ports=rec['buffers'][0];physical=ports['out' if c['type']=='IBUF' else 'in']
        if not physical or physical[2] not in device.names:raise ValueError('Unmapped I/O routing endpoint')
        fixed[name]=np.array([physical[0]-1,physical[1]-1],dtype=float);unit[name]=name
        pin_bindings[name]=dict(port=port,package_pin=pin,physical_site=loc,native_ports=ports,electrical=contract['properties'].get(port,''))
    drivers={};sinks=defaultdict(list)
    for name,c in cells.items():
        for pin,bs in c['connections'].items():
            if name not in unit:continue
            if c['type']=='IBUF' and pin=='I' or c['type']=='OBUF' and pin=='O':continue
            for index,bit in enumerate(bs):
                label=pin if len(bs)==1 else f'{pin}[{index}]'
                if name in hard_names and label not in hard[name]['endpoints']:continue
                if bit in constants:continue
                if c['port_directions'][pin]=='output':drivers[bit]=(name,pin)
                else:sinks[bit].append((name,pin))
    # Coarse depth criticality is used only for initial placement weights;
    # it is not reported as STA. FF outputs terminate combinational recursion.
    memo={};active=set()
    def depth(name):
        if name in memo:return memo[name]
        if name in active:raise ValueError('Combinational cycle')
        c=cells[name]
        if not c['type'].startswith(('LUT','MUX2_LUT')) and c['type'] not in ('ALU','MULTALU27X18'):return 0
        active.add(name);value=1+max((depth(drivers[b][0]) for p,bs in c['connections'].items() if c['port_directions'][p]=='input' and (c['type']!='MULTALU27X18' or p in ('A','B')) for b in bs if b in drivers),default=0);active.remove(name);memo[name]=value;return value
    for n in groups:depth(n)
    for chain in macros.values():
        for n in chain['cells']:depth(n)
    for root in wides:depth(root)
    maximum=max(memo.values(),default=1);edges=[];adj=defaultdict(list)
    clockbits={c['connections']['CLK'][0] for c in cells.values() if c['type'] in FF_CONTROLS}
    for name,spec in (hard or {}).items():
        for pin in spec['clock_pins']:
            endpoint=spec['endpoints'][pin];clockbits.add(cells[name]['connections'][endpoint['port']][endpoint['index']])
    for bit,loads in sinks.items():
        if bit in clockbits:continue
        if bit not in drivers:raise ValueError('Undriven physical signal '+str(bit)+' loads '+str(loads[:4]))
        dn,_=drivers[bit];a=unit[dn];weight=(1+4*(depth(dn)/maximum)**2)/max(1,len(loads))
        for sn,pin in loads:
            b=unit[sn]
            if a==b:continue
            criticality=(critical_endpoints or {}).get((sn,pin),0.)
            edge_weight=weight*(1+9*criticality**2)
            edges.append((a,b,edge_weight));adj[a].append((b,edge_weight));adj[b].append((a,edge_weight))
    clock_candidates=np.array(sorted(clock_sites),dtype=np.int32) if clock_sites is not None else None
    if clock_sites is not None and not len(clock_sites):raise ValueError('No sites satisfy the clock-placement budget')
    movable=(set(groups)|set(macros)|set(wides)|hard_names)-set(anchored);centroid=np.mean(list(fixed.values()),axis=0);pos={n:centroid.copy() for n in movable};pos.update(fixed)
    for iteration in range(80):
        for name in sorted(movable):
            neighbors=adj[name]
            if neighbors:
                p=sum((pos[n]*w for n,w in neighbors),np.zeros(2))/sum(w for _,w in neighbors)
                pos[name]=.5*pos[name]+.5*p
                if clock_candidates is not None and name in groups and groups[name]['ff']:
                    # Project FF clusters into measured clock regions during
                    # relaxation so their combinational neighbors follow.
                    nearest=np.argmin(((clock_candidates-pos[name])**2).sum(axis=1))
                    pos[name]=clock_candidates[nearest].astype(float)
    sites=np.array([(r-1,c-1) for r,row in enumerate(native) for c,kind in enumerate(row) if kind==5],dtype=np.int32)
    if len(sites)!=17280:raise ValueError('Native CFU inventory differs from expected device capacity')
    reserved=set();arithmetic={};hard_placed={}
    if hard:
        from ownpr_hard_architecture import HardArchitecture
        hard_placed=HardArchitecture().legalize(hard,pos,{n:sum(w for _,w in adj[n]) for n in hard})
        for name,loc in hard_placed.items():
            pos[name]=np.array([loc['row'],loc['column']+1],dtype=float)
            reserved.update((loc['row'],loc['column']+j) for j in range(3))
        # Re-relax surrounding logic with legal hard sites fixed in place.
        for _ in range(20):
            for name in sorted(movable-hard_names):
                neighbors=adj[name]
                if neighbors:pos[name]=.5*pos[name]+.5*sum((pos[n]*w for n,w in neighbors),np.zeros(2))/sum(w for _,w in neighbors)
    for key in sorted(macros,key=lambda n:(-macros[n]['length'],n)):
        chain=macros[key];width=(chain['length']+5)//6;options=[]
        for row,column in sites:
            r,c=int(row),int(column)
            if c+width>device.cols or any(device.grid[r][c+j]!=17 or (r,c+j) in reserved or (r,c+j) in anchor_tiles for j in range(width)):continue
            cost=(r-pos[key][0])**2+(c+(width-1)/2-pos[key][1])**2;options.append((cost,r,c))
        if not options:raise ValueError('No contiguous calibrated CFU run for carry chain')
        _,r,c=min(options);reserved.update((r,c+j) for j in range(width))
        for i,name in enumerate(chain['cells']):arithmetic[name]=dict(row=r,column=c+i//6,slot=i%6,chain=key,**carry['profiles'][name])
    occupancy={point:name for name,point in anchored.items()};ff_groups={(r,c,s//2) for r,c,s in anchored.values()};placed={name:dict(row=r,column=c,slot=s,lut=name,ff=groups[name]['ff']) for name,(r,c,s) in anchored.items()};order=sorted(set(groups)-set(anchored),key=lambda n:(groups[n]['ff'] is None,-sum(w for _,w in adj[n]),n))
    muxes={};wide_macros=[]
    for root in sorted(wides,key=lambda n:(-wides[n]['level'],n)):
        options=[((r-pos[root][0])**2+(c-pos[root][1])**2,int(r),int(c)) for r,c in sites if device.grid[r][c]==17 and (r,c) not in reserved and (r,c) not in anchor_tiles]
        if not options:raise ValueError('No free calibrated wide-macro CFU')
        _,r,c=min(options);reserved.add((r,c));spec=wides[root]
        for n,slot in spec['leaves'].items():placed[n]=dict(lut=n,ff=None,row=r,column=c,slot=slot)
        muxes.update({n:dict(row=r,column=c,**x) for n,x in spec['muxes'].items()});wide_macros.append(dict(root=root,row=r,column=c,root_slot=spec['muxes'][root]['slot']))
    for name in order:
        distance=((sites-pos[name])**2).sum(axis=1);choices=np.argsort(distance,kind='stable');ff=groups[name]['ff'];done=False
        for index in choices:
            r,c=map(int,sites[index])
            if (r,c) in reserved:continue
            if ff and clock_sites is not None and (r,c) not in clock_sites:continue
            for slot in ((0,2,4) if ff else range(8)):
                if ff:
                    # Admit only an output with explicitly decoded ordinary
                    # switch access. Other FF modes need native mode/alias
                    # decoding before they can enter this initial flow.
                    records=device.routing_connections(device.grid[r][c])
                    if not any(e['source_name']==f'Q{slot}' and e['destination_name'] and re.match(r'(?:[NESW][128]|X0)',e['destination_name']) for e in records):continue
                if (r,c) in extended_anchor_tiles:continue
                if (r,c,slot) in occupancy or ff and (r,c,slot//2) in ff_groups:continue
                occupancy[r,c,slot]=name;placed[name]=dict(row=r,column=c,slot=slot,lut=name,ff=ff)
                if ff:ff_groups.add((r,c,slot//2))
                done=True;break
            if done:break
        if not done:raise ValueError('No legal native CFU slot')
    wirelength=sum((abs(placed[a]['row']-placed[b]['row'])+abs(placed[a]['column']-placed[b]['column']))*w for a,b,w in edges if a in placed and b in placed)
    result=dict(clusters=placed,arithmetic_clusters=arithmetic,fixed_io=pin_bindings,clock_routing_constraints=contract['clock_routing'],weighted_internal_manhattan=wirelength,capacity_legal=True,physical_legality_complete=False,
        full_device_support=False,timing_signoff=False,clock_placement_optimized=clock_sites is not None,
        scope='Own weighted analytical placement + capacity legalizer on native CFU sites. Initial LUT4/DFFRE slice; one FF per CLS.')
    if wides:result.update(mux_clusters=muxes,wide_macros=wide_macros)
    if ff_sites is not None:result['fixed_ff_sites']={name:list(site) for name,site in sorted(ff_sites.items())}
    if hard:result.update(hard_clusters=hard_placed,hard_placement_method='joint_relaxation_minimum_cost_exclusive_assignment')
    if ff_sites is not None and any(p[2] not in (0,2,4) for p in ff_sites.values()):
        from ownpr_ff_configuration import single_ff_any_slot_calibration
        result['single_ff_any_slot_calibration']=single_ff_any_slot_calibration()
    return result

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--netlist',type=Path,required=True);ap.add_argument('--top',default='top');ap.add_argument('--cst',type=Path,required=True);ap.add_argument('--sdc',type=Path,required=True);ap.add_argument('--runtime',type=Path,default=ROOT/'results/native_route_runtime_007');ap.add_argument('--out',type=Path,required=True);a=ap.parse_args();out=a.out.resolve();out.mkdir(exist_ok=False);start=time.monotonic()
    before=json.loads(a.netlist.read_text());contract=constraints(before['modules'][a.top],a.cst.read_text(),a.sdc.read_text());design,groups,constants,packing=pack(before,a.top)
    proof=prove(before,design,out/'packing_cec',120);d=Device();placement=place(design,a.top,groups,constants,contract,d,a.runtime)
    (out/'packed.json').write_text(json.dumps(design));(out/'placement.json').write_text(json.dumps(placement,indent=2))
    record=dict(status='placed',source_sha256=sha(a.netlist),cst_sha256=sha(a.cst),sdc_sha256=sha(a.sdc),packing=packing,proof=proof,
        period_ns=contract['period_ns'],open_chip_database_used=False,all_ports_fixed=True,all_nonclock_io_max_min_constrained=True,
        clusters=len(groups),io_buffers=len(placement['fixed_io']),wall_seconds=time.monotonic()-start,timing_signoff=False)
    (out/'manifest.json').write_text(json.dumps(record,indent=2));print({k:v for k,v in record.items() if k not in ('packing','proof')})
if __name__=='__main__':main()
