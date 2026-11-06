"""Route the independently packed/placed initial LUT4+DFFRE design slice."""
import argparse,json,time
from collections import defaultdict
from pathlib import Path
from ownpr_device import Device,RoutingGraph,ROOT
from ownpr_router import route,validate
from ownpr_place import sha
from ownpr_ff_configuration import compile_initial,FF_CONTROLS

def bind(design,placement,graph,top='top',hard_bindings=None,logical_sinks=None):
    if logical_sinks is not None and logical_sinks:raise ValueError('Logical sink collector must be empty')
    cells=design['modules'][top]['cells'];binding={};constants={'0':0,'1':1}
    from ownpr_wide_ff_packing import configure_graph
    configure_graph(graph,design,placement)
    ff_configuration=compile_initial(design,placement,top);static=list(ff_configuration['static_control_pins'])
    static_control_pins={(p['cell'],p['pin']) for p in static}
    for c in cells.values():
        if c['type'] in ('VCC','GND'):constants[c['connections']['V' if c['type']=='VCC' else 'G'][0]]=int(c['type']=='VCC')
    arithmetic_skip=set()
    mux_skip=set()
    ioreg_skip=set()
    if placement.get('ioreg_clusters'):
        from ownpr_ioreg_macros import validate as validate_ioreg
        for row in validate_ioreg(design,placement,graph.device)['records']:
            ioreg_skip.update(((row['instance'],'Q'),(row['iobuf'],'I')))
            static.append(dict(kind='ioreg_output_link',**row['link'],site=row['site'],configuration_mode_verified=False))
            if row['ce_mode']=='one':
                static_control_pins.add((row['instance'],'CE'))
                static.append(dict(kind='ioreg_static_ce',cell=row['instance'],pin='CE',value=1,physical_node=row['pins']['CE'],
                    configuration_mode_verified=True,full_bitstream_verified=False))
            for pin,point in row['pins'].items():
                if point is None:continue
                r,c,node=point;binding[row['instance'],pin]=(graph.node(r-1,c-1,graph.device.names[node]),(r-1,c-1))
    if placement.get('mux_clusters'):
        from ownpr_mux_architecture import MuxArchitecture
        from ownpr_mux_macros import validate as validate_mux
        model=MuxArchitecture();check=validate_mux(design,placement,graph.device)
        for link in check['links']:
            mux_skip.add((link['destination'],link['pin']));static.append(dict(kind='mux_dedicated_link',**link,
                geometry_verified=True,configuration_mode_verified=False))
        for name,loc in placement['mux_clusters'].items():
            r,c,slot=loc['row'],loc['column'],loc['slot'];pins=model.pins(graph.device.grid[r][c],loc['level'],slot)
            for pin in ('S0','O'):binding[name,pin]=(graph.node(r,c,pins[pin]),(r,c))
    if placement.get('arithmetic_clusters'):
        from ownpr_alu_architecture import AluArchitecture
        from ownpr_carry_macros import validate as validate_carry
        model=AluArchitecture();carry=validate_carry(design,placement,graph.device,top)
        for link in carry['links']:
            arithmetic_skip.add((link['destination'],'CIN'))
            record=dict(link);record.update(kind='dedicated_carry_link',connection_kind=link['kind'],cell=link['destination'],pin='CIN',
                configuration_mode_verified=False,connectivity_pattern_checked=True);static.append(record)
        for name,loc in placement['arithmetic_clusters'].items():
            r,c,slot=loc['row'],loc['column'],loc['slot'];physical=model.pins(graph.device.grid[r][c],slot,loc['mode']);cell=cells[name]
            for pin,bits in cell['connections'].items():
                if pin=='COUT':arithmetic_skip.add((name,pin));continue
                if bits[0] in constants:
                    arithmetic_skip.add((name,pin));static.append(dict(kind='alu_constant_fold',cell=name,pin=pin,value=constants[bits[0]],
                        configuration_mode_verified=True,configuration_fragment_verified=True,bitstream_verified=False));continue
                if (name,pin) in arithmetic_skip:continue
                if pin=='CIN':raise ValueError('Uncovered dedicated carry endpoint')
                binding[name,pin]=(graph.node(r,c,physical[pin]),(r,c))
    for group in placement['clusters'].values():
        r,c,i=group['row'],group['column'],group['slot'];lut=group['lut'];ff=group['ff']
        for pin in cells[lut]['connections']:
            wire=f'F{i}' if pin=='F' else 'ABCD'[int(pin[1:])]+str(i);binding[lut,pin]=(graph.node(r,c,wire),(r,c))
        if ff:
            physical=graph.device.register_bindings_for(cells[ff]['type'])[i]
            for pin in cells[ff]['connections']:
                # D remains the initial packer's unverified internal LUT->FF
                # connection. Do not confuse this with native DI/mux signoff.
                wire=(f'DI{i}' if group.get('wide_ff_root') else f'F{i}') if pin=='D' else physical[pin]
                binding[ff,pin]=(graph.node(r,c,wire),(r,c))
    for name,io in placement['fixed_io'].items():
        for pin,field in ([('O','out')] if cells[name]['type']=='IBUF' else [('I','in')]):
            r,c,i=io['native_ports'][field];r-=1;c-=1;binding[name,pin]=(graph.node(r,c,graph.device.names[i]),(r,c))
    from ownpr_hard_architecture import HARD_TYPES
    hard_cells={n for n,c in cells.items() if c['type'] in HARD_TYPES};hard={}
    if hard_cells:
        if not hard_bindings or hard_bindings.get('status')!='selected_hard_endpoints_verified' or set(hard_bindings['cells'])!=hard_cells:
            raise ValueError('Hard routing requires audited selected-site endpoints')
        hard=hard_bindings['cells']
    signals=defaultdict(lambda:dict(drivers=[],sinks=[],source_sites=[]));clockbits={c['connections']['CLK'][0] for c in cells.values() if c['type'] in FF_CONTROLS}
    for name,pins in hard.items():
        for row in pins.values():
            if row['nodes'] and row['port'] in ('CLK','CLKA','CLKB'):clockbits.add(cells[name]['connections'][row['port']][row['index']])
    for name,c in cells.items():
        if name in hard:
            for label,row in hard[name].items():
                bit=c['connections'][row['port']][row['index']]
                if not row['nodes']:
                    static.append(dict(kind='hard_inactive_pin',cell=name,pin=label,reason=row['inactive_reason'],configuration_mode_verified=False));continue
                key=f'constant_{constants[bit]}' if bit in constants else f'net_{bit}';signal=signals[key]
                if row['direction']=='output' and len(row['nodes'])!=1:raise ValueError('Hard output is not a unique physical driver')
                for r,col,node_id in row['nodes']:
                    node=graph.node(r-1,col-1,graph.device.names[node_id]);site=(r-1,col-1)
                    signal['drivers' if row['direction']=='output' else 'sinks'].append(node)
                    if logical_sinks is not None and row['direction']!='output':logical_sinks.append(dict(instance=name,pin=label,node=node,net=key))
                    if bit in constants:signal['constant_value']=constants[bit];signal['source_sites'].append(site)
                if bit in clockbits:signal['clock']=True
            continue
        if c['type'] in FF_CONTROLS:clockbits.add(c['connections']['CLK'][0])
        if c['type']=='GSR':
            if constants.get(c['connections']['GSRI'][0])!=1:raise ValueError('Active GSR unsupported')
            static.append(dict(cell=name,pin='GSRI',value=1,configuration_verified=False));continue
        if c['type'] in ('VCC','GND'):continue
        for pin,bits in c['connections'].items():
            if (name,pin) in arithmetic_skip or (name,pin) in mux_skip or (name,pin) in ioreg_skip:continue
            if (name,pin) in static_control_pins:continue
            if (name,pin) not in binding:
                if c['type']=='IBUF' and pin=='I' or c['type']=='OBUF' and pin=='O':continue
                raise ValueError('Missing physical pin binding')
            if len(bits)!=1:raise ValueError('Expected scalar primitive pins')
            bit=bits[0];node,site=binding[name,pin];key=f'constant_{constants[bit]}' if bit in constants else f'net_{bit}'
            if c['type'] in ('LUT1','LUT2','LUT3','LUT4') and pin.startswith('I') and bit in constants:
                from ownpr_lut_lifting import independent
                if independent(c,int(pin[1:])):
                    static.append(dict(kind='lut_truth_independent_constant_input',cell=name,pin=pin,value=constants[bit],
                        truth_table_independence_proven=True,configuration_mode_verified=True,full_bitstream_verified=False))
                    continue
            signal=signals[key]
            if c['port_directions'][pin]=='output':signal['drivers'].append(node)
            else:
                signal['sinks'].append(node)
                if logical_sinks is not None:logical_sinks.append(dict(instance=name,pin=pin,node=node,net=key))
            if bit in constants:
                value=constants[bit];signal['constant_value']=value;signal['source_sites'].append(site)
            if bit in clockbits:signal['clock']=True
    nets=[]
    for name,s in sorted(signals.items()):
        if not s['sinks']:continue
        if 'constant_value' in s:
            sites=sorted(set(s['source_sites']));wire='VCC' if s['constant_value'] else 'VSS';sources=[graph.node(r,c,wire) for r,c in sites]
            row=dict(name=name,source=sources[0],sources=sources,source_sites=sites,constant_value=s['constant_value'],sinks=sorted(set(s['sinks'])),criticality=0.)
        else:
            drivers=set(s['drivers'])
            if len(drivers)!=1:raise ValueError('Signal does not have exactly one physical driver: '+name)
            row=dict(name=name,source=drivers.pop(),sinks=sorted(set(s['sinks'])),criticality=1. if s.get('clock') else .5,clock=bool(s.get('clock')))
        nets.append(row)
    return nets,static

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--placement',type=Path,required=True);ap.add_argument('--rrg',type=Path,default=ROOT/'results/native_rrg_001');ap.add_argument('--out',type=Path,required=True);ap.add_argument('--timeout',type=int,default=600);a=ap.parse_args();out=a.out.resolve();out.mkdir(exist_ok=False)
    if not 0<a.timeout<=3600:raise ValueError('Router timeout must not exceed one hour')
    start=time.monotonic();d=Device();g=RoutingGraph(d,a.rrg)
    packed=a.placement/'packed.json';placefile=a.placement/'placement.json';design=json.loads(packed.read_text());placement=json.loads(placefile.read_text())
    nets,static=bind(design,placement,g);(out/'nets.json').write_text(json.dumps(nets,indent=2))
    meta=dict(status='routing',netlist_sha256=sha(packed),placement_sha256=sha(placefile),open_chip_database_used=False,vendor_pnr_executed=False,net_count=len(nets),
        static_configuration_pending=[p for p in static if not p.get('configuration_mode_verified')],
        constant_control_configuration=[p for p in static if p.get('configuration_mode_verified')],timing_signoff=False,bitstream_generated=False)
    (out/'manifest.json').write_text(json.dumps(meta,indent=2));print('Routing',len(nets),'physical nets',flush=True)
    def progress(row):
        with (out/'progress.jsonl').open('a') as log:log.write(json.dumps(row)+'\n')
        if row['event']=='iteration_done' or row.get('failed'):print(json.dumps(row),flush=True)
    try:
        result=route(g,nets,max_iterations=30,timeout=max(1,a.timeout-(time.monotonic()-start)),max_expansions=50000,progress=progress)
        (out/'routes.json').write_text(json.dumps(result))
        meta['status']=result['status'];meta['iterations']=result['iterations']
        if result['status']=='routed':meta['connectivity_audit']=validate(g,nets,result)
    except Exception as e:meta.update(status='failed',error=repr(e))
    meta.update(wall_seconds=time.monotonic()-start,scope='Own initial LUT4/DFFRE/fixed-I/O placement and normal-wire routing. Config-fuse conflicts, dedicated clocks, complete STA and bitstream are not yet validated.')
    (out/'manifest.json').write_text(json.dumps(meta,indent=2));print(json.dumps(meta,indent=2),flush=True)
if __name__=='__main__':main()
