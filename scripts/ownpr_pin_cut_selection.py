"""Discover timing cuts from all checked output-pin slacks, not path lists.

Old loaded wires and calibrated cell arcs rank local cones. They do not
predict new placement, loading, or complete timing. Only replaced roots are
covered; preserved shared interior nodes are never declared optimized.
"""
import math
from ownpr_multilevel_cut import Cuts,wide_absorption_boundaries
from ownpr_critical_chain_move import exclusive_tiles
from ownpr_image_storage import load_json,sha


def rebuild_receivers(routes,placement,meta):
    """Reconstruct receivers for routes made without historical weights."""
    from collections import defaultdict
    from ownpr_device import Device,RoutingGraph,ROOT
    from ownpr_route_design import bind
    from ownpr_incremental_routes import signature
    if placement is None:raise ValueError('Receiver binding capture unavailable; provide the checked placement')
    if sha(placement/'packed.json')!=meta['netlist_sha256'] or sha(placement/'placement.json')!=meta['placement_sha256']:raise ValueError('Receiver reconstruction placement differs')
    hard=None;sources=dict(netlist_sha256=meta['netlist_sha256'],placement_sha256=meta['placement_sha256'],all_nets_sha256=sha(routes/'all_nets.json'))
    if meta.get('hard_cells'):
        file=routes/'hard_endpoint_audit.json'
        if sha(file)!=meta['hard_endpoint_audit_sha256']:raise ValueError('Hard receiver binding changed')
        hard=load_json(file);sources['hard_endpoint_audit_sha256']=sha(file)
    graph=RoutingGraph(Device(),ROOT/'results/native_rrg_001');receivers=[]
    actual,_=bind(load_json(placement/'packed.json'),load_json(placement/'placement.json'),graph,hard_bindings=hard,logical_sinks=receivers)
    a={n['name']:n for n in actual};b={n['name']:n for n in load_json(routes/'all_nets.json')}
    if set(a)!=set(b) or any(signature(a[n])!=signature(b[n]) or bool(a[n].get('clock'))!=bool(b[n].get('clock')) for n in a):raise ValueError('Reconstructed physical terminals differ')
    points={(n['name'],s) for n in load_json(routes/'nets.json') if not n.get('clock') and 'constant_value' not in n for s in n['sinks']};groups=defaultdict(set)
    for row in receivers:
        key=row['net'],row['node']
        if key in points:groups[key].add((row['instance'],row['pin']))
    if set(groups)!=points:raise ValueError('Receiver reconstruction lacks current data terminals')
    return [dict(net=n,node=s,receivers=sorted(groups[n,s])) for n,s in sorted(groups)],dict(source='Hash-bound placement/hard binding; exact all-net physical signatures checked',**sources)


def measured_inputs(routes,wire_folder,placement=None):
    wire=load_json(wire_folder/'wire_delays.json');meta=load_json(routes/'manifest.json')
    if wire['source_routes_sha256']!=sha(routes/'routes.json') or wire['source_nets_sha256']!=sha(routes/'nets.json') or wire['vendor_pnr_executed']:
        raise ValueError('Old wire measurements do not match own routing')
    if 'search_weights_sha256' in meta:
        if meta['search_weights_sha256']!=sha(routes/'search_weights.json'):raise ValueError('Receiver binding changed')
        receivers=load_json(routes/'search_weights.json')['policy']['receivers'];binding_info=dict(receiver_binding_sha256=sha(routes/'search_weights.json'))
    else:
        receivers,rebuilt=rebuild_receivers(routes,placement,meta);binding_info=dict(reconstructed_receiver_binding=rebuilt)
    delays={(net,r['resource']):r['setup_wire_ns'] for net,v in wire['nets'].items() for r in v['sinks']};result={}
    for row in receivers:
        point=row['net'],row['node']
        if point not in delays:raise ValueError('Receiver lacks whole-tree measurement')
        for pin in row['receivers']:
            key=tuple(pin)
            if key in result:raise ValueError('Ambiguous receiver binding')
            result[key]=delays[point]
    return result,dict(wire_sha256=sha(wire_folder/'wire_delays.json'),routes_sha256=sha(routes/'routes.json'),**binding_info)


def cone_paths(model,cut,wire,cell_delay):
    boundary=set(cut['evaluation_boundary']);inside=set(cut['interior']);cache={};active=set()
    def walk(name):
        if name in cache:return cache[name]
        if name in active:raise ValueError('Cyclic cut')
        active.add(name);result={};c=model.cells[name];support=set(model.functions[name][0])
        for pin,bs in c['connections'].items():
            if c['port_directions'][pin]!='input':continue
            bit=bs[0]
            if bit not in support:continue
            dedicated=c['type'].startswith('MUX2_LUT') and pin in ('I0','I1')
            w=0. if dedicated else wire.get((name,pin))
            if w is None:raise ValueError('Cut input has no measured routing delay')
            intrinsic=cell_delay(name,pin)
            if not math.isfinite(w+intrinsic) or min(w,intrinsic)<0:raise ValueError('Invalid old delay')
            if bit in boundary:entries={bit:(w+intrinsic,w)}
            else:
                driver=model.drivers.get(bit)
                if driver not in inside:raise ValueError('Cut boundary mismatch')
                entries={b:(v[0]+w+intrinsic,v[1]) for b,v in walk(driver).items()}
            for b,value in entries.items():
                if b not in result or value[0]>result[b][0]:result[b]=value
        active.remove(name);cache[name]=result;return result
    return walk(cut['root'])


def discover(design,layout,budgets,wire,device,window=4.,cut_width=7,min_cells=2,max_roots=256,reported_roots=(),arrival_values=None):
    if not 1<=max_roots<=1024 or not 0<window<=5:raise ValueError('Invalid critical-root bounds')
    from ownpr_cell_timing import NativeCellTiming
    from ownpr_mux_architecture import MuxArchitecture
    lib=NativeCellTiming();mux=MuxArchitecture();model=Cuts(design,k=cut_width);cells=model.cells;excluded=exclusive_tiles(layout)
    clock_cells=set();pending=[b for c in cells.values() for p,bs in c['connections'].items() if c['port_directions'][p]=='input' and p.startswith('CLK') for b in bs];seen_clock=set()
    while pending:
        bit=pending.pop()
        if bit in seen_clock:continue
        seen_clock.add(bit);n=model.drivers.get(bit)
        if n in model.functions or n in cells and cells[n]['type'] in ('BUFG','GBUF','CLKBUF','IBUF'):
            clock_cells.add(n);pending.extend(b for p,bs in cells[n]['connections'].items() if cells[n]['port_directions'][p]=='input' for b in bs)
    roots=[]
    for name,row in layout['clusters'].items():
        slack=budgets.get((name,'F'),{}).get('original_setup_slack_ns')
        if name not in clock_cells and cells[name]['type'] in ('LUT1','LUT2','LUT3','LUT4') and (row['row'],row['column']) not in excluded and slack is not None and math.isfinite(slack):roots.append((slack,name))
    if not roots:raise ValueError('No supported output-pin slacks')
    worst=min(x[0] for x in roots);eligible=sorted(x for x in roots if x[0]<=worst+window);chosen_roots=eligible[:max_roots]
    private={b for c in cells.values() if c['type'].startswith('MUX2_LUT') for p in ('I0','I1') for b in c['connections'][p]};options=[];rejected=[];seen=set()
    drivers={b:(n,p if len(bs)==1 else f'{p}[{i}]') for n,c in cells.items() for p,bs in c['connections'].items() if c['port_directions'][p]=='output' for i,b in enumerate(bs)}
    def cell_delay(name,pin):
        if cells[name]['type'].startswith('LUT'):
            return max(lib.lut_input(int(pin[1:]),layout['clusters'][name]['slot'],1)[2:4])
        loc=layout['mux_clusters'][name];typ=device.grid[loc['row']][loc['column']]
        return max(mux.delay(typ,loc['level'],loc['slot'],pin,1)[2:4])
    for slack,root in chosen_roots:
        boundaries=list(wide_absorption_boundaries(design,model,root)) if cut_width==7 else []
        boundaries+=list(model.enumerate(model.functions[root][1]))
        for boundary in boundaries:
            if not boundary or model.functions[root][1] in boundary:continue
            cut=model.function(root,boundary);width=cut['cut_width'];key=(root,tuple(cut['cut_bits']),cut['truth_hex'])
            if key in seen or cut['interior_count']<min_cells or not 1<=width<=cut_width or set(cut['cut_bits'])&private or set(cut['interior'])&clock_cells:continue
            seen.add(key)
            if width==7 and layout['clusters'][root].get('ff'):continue
            try:paths=cone_paths(model,cut,wire,cell_delay)
            except (ValueError,KeyError) as error:
                rejected.append(dict(root=root,reason=str(error)));continue
            priorities=[]
            for bit,(total,first_wire) in paths.items():
                if bit not in cut['cut_bits']:continue
                value=budgets.get(drivers.get(bit),{}).get('original_setup_slack_ns');effective=max(slack,value) if value is not None else slack
                priorities.append((effective,-(total-first_wire),bit,total,first_wire))
            if not priorities:continue
            effective,_,late,total,first_wire=min(priorities)
            new_cell=max(lib.lut_input(width-1,layout['clusters'][root]['slot'],1)[2:4]) if width<=4 else max(mux.delay(17,width,{5:0,6:1,7:3}[width],'S0',1)[2:4])
            gain=total-first_wire-new_cell
            if gain<=.05:continue
            legacy_gain=gain;competition=None
            if arrival_values is not None:
                from ownpr_cut_competition import rank
                competition=rank(cut,paths,drivers,arrival_values,lib,mux,layout['clusters'][root]['slot'])
                if competition is None:
                    rejected.append(dict(root=root,reason='Missing competing boundary arrival'));continue
                gain=competition['selected']['gain_proxy_ns'];late=competition['selected']['late_bit']
            options.append(dict(cut=cut,late_bit=late,worst_slack_ns=slack,root_setup_slack_ns=slack,priority_gain_proxy_ns=gain,
                legacy_priority_gain_proxy_ns=legacy_gain,competing_arrivals=competition,
                old_cone_internal_delay_ns=total-first_wire,new_final_cell_delay_ns=new_cell,critical_boundary_slack_hint_ns=effective,
                lut_growth=max(0,(1<<max(0,width-4))-1),paired_ff=layout['clusters'][root].get('ff'),witness_paths=[],priority_proxy_ns={},
                root_was_in_reported_paths=root in set(reported_roots),ranking_scope='Competing arrivals with inherited wires; new routing unpredicted' if competition else 'Old local internal delay minus new final cell only; new wires and competing boundary arrivals are unpredicted'))
    return options,dict(eligible_roots=len(eligible),considered_roots=len(chosen_roots),max_roots=max_roots,deferred_roots=max(0,len(eligible)-max_roots),
        additional_roots_not_in_reported_paths=[n for _,n in chosen_roots if n not in set(reported_roots)],candidate_count=len(options),rejected=rejected,cell_library_sha256=lib.sha256,
        coverage_scope='Checked root output pins in a bounded window, not endpoint closure or all alternate paths')


def select_roots(options,count=8,max_growth=48):
    if not options or not 1<=count<=16 or not 0<=max_growth<=48:raise ValueError('Invalid root selection')
    worst=min(r['worst_slack_ns'] for r in options);chosen=[];growth=0
    while len(chosen)<count:
        possible=[]
        for row in options:
            root=row['cut']['root'];inside=set(row['cut']['interior'])
            if growth+row['lut_growth']>max_growth:continue
            # Only roots are rewritten. Shared preserved prefixes may coexist;
            # a root inside another cut would invalidate that cut certificate.
            if any(root in prev['cut']['interior'] or prev['cut']['root'] in inside for prev in chosen):continue
            bucket=math.floor(max(0.,row['worst_slack_ns']-worst)/.25)
            score=(bucket,-row['priority_gain_proxy_ns']/(1+.05*row['lut_growth']),row['worst_slack_ns'],row['lut_growth'],root)
            possible.append((score,row))
        if not possible:break
        _,row=min(possible,key=lambda x:x[0]);chosen.append(row);growth+=row['lut_growth']
    return chosen,dict(selected_roots=[r['cut']['root'] for r in chosen],lut_growth=growth,coverage_definition='Only selected root functions rewritten; no path or endpoint is declared closed',
        shared_preserved_interiors_allowed=True,root_dependency_conflicts_rejected=True)
