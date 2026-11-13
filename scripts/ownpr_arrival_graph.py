"""Independent slow-corner combinational arrival propagation for own placements.

The graph uses calibrated native-derived cell arcs and whole-tree wire queries.
IO delays currently use an explicitly labelled observed electrical-mode envelope.
This is optimization feedback, not replacement signoff: hold, exceptions and
general clock/primitive modes remain the final native STA's responsibility.
"""
from ownpr_image_storage import read_text as read_artifact_text,load_json,available
import argparse, collections, hashlib, json, math, re, time
from pathlib import Path
from extract_timing import ROW
from ownpr_place import sha
from ownpr_timing_pins import pins as timing_pins


def ioreg_clock_to_q(data,placed,aliases):
    """Extract slow maximum Q arcs after the independent I/O-mode audit."""
    expected={aliases[n]:n for n in placed.get('ioreg_clusters',{})};result={}
    for row in data['registers']:
        alias=row['instance'];name=expected.get(alias)
        if name is None or name in result:raise ValueError('Unexpected/duplicate I/O arrival register')
        site=placed['ioreg_clusters'][name]['site']
        if row['site']!=[0,site[0],site[1],1] or row['clock_positive'] is not True:raise ValueError('Unsupported I/O arrival clock/site')
        queries=[q for q in row['queries'] if q['pin']=='Q' and q['selector']==1 and q['maximum'] is True]
        if len(queries)!=1:raise ValueError('Missing/duplicate I/O clock-to-Q query')
        values=queries[0]['values']
        if len(values)!=4 or any(not isinstance(v,(int,float)) or not math.isfinite(v) or not 0<=v<1e9 for v in values):raise ValueError('Invalid I/O clock-to-Q values')
        result[name]=values[2:4]
    if set(result)!=set(expected.values()):raise ValueError('Incomplete I/O arrival register coverage')
    return result


def senses(truth, width, constants):
    result = {}
    for pin in range(width):
        if pin in constants: continue
        allowed = set()
        for assignment in range(1 << width):
            if assignment >> pin & 1: continue
            if any((assignment >> i & 1) != value for i, value in constants.items()): continue
            a, b = truth >> assignment & 1, truth >> (assignment | (1 << pin)) & 1
            if a != b: allowed.update(((0, 1-b), (1, 1-a)))
        if allowed: result[pin] = sorted(allowed)
    return result


def propagate(nodes, edges, seeds, overrides=None):
    """Rise/fall DAG max-plus propagation, independent of reported worst paths."""
    incoming = collections.defaultdict(list); outgoing = collections.defaultdict(list)
    degree = dict.fromkeys(nodes, 0)
    for index, edge in enumerate(edges):
        source, dest = edge['source'], edge['destination']
        if source not in degree or dest not in degree: raise ValueError('Unknown timing vertex')
        incoming[dest].append((index, edge)); outgoing[source].append(dest); degree[dest] += 1
    queue = collections.deque(sorted(n for n, d in degree.items() if not d))
    values = {n: [-math.inf, -math.inf] for n in nodes}; parents = {}; candidates = {}
    for node, seed in seeds.items():
        if node not in values or len(seed) != 2: raise ValueError('Unknown/malformed boundary seed')
        values[node] = list(seed)
    visited = 0
    while queue:
        node = queue.popleft(); visited += 1
        for index, edge in incoming[node]:
            delay = (overrides or {}).get(index, edge['delay'])
            if len(delay) != 2 or any(not math.isfinite(d) or d < 0 for d in delay): raise ValueError('Invalid timing arc delay')
            for source_tr, dest_tr in edge['transitions']:
                value = values[edge['source']][source_tr] + delay[dest_tr]
                if math.isfinite(value):
                    candidates.setdefault(node, []).append(dict(edge=index, source=edge['source'],
                        source_transition=source_tr, transition=dest_tr, arrival_ns=value))
                if value > values[node][dest_tr]:
                    values[node][dest_tr] = value; parents[node, dest_tr] = index, source_tr
        for dest in outgoing[node]:
            degree[dest] -= 1
            if not degree[dest]: queue.append(dest)
    if visited != len(nodes): raise ValueError('Combinational timing cycle')
    return values, parents, candidates


def report_paths(text):
    section = re.search(r'^3\.3\.1 Setup Analysis Report\n(.*?)(?=^3\.|\Z)', text, re.M|re.S)
    if not section: raise ValueError('Missing complete setup report')
    blocks = re.split(r'^\s*Path(\d+)\s*$', section[1], flags=re.M); paths = []
    for i in range(1, len(blocks), 2):
        body = blocks[i+1]; trace = re.search(r'Data Arrival Path:\n(.*?)(?=\nData Required Path:)', body, re.S)
        if not trace: raise ValueError('Missing arrival trace')
        rows = [m.groups() for line in trace[1].splitlines() if (m := ROW.match(line))]
        summary = {k: float(re.search('^'+re.escape(k)+r'\s*:\s*([-\d.]+)', body, re.M)[1])
                   for k in ('Slack', 'Data Arrival Time', 'Data Required Time')}
        paths.append(dict(path=int(blocks[i]), rows=rows, **summary))
    return paths


def build(placement, sta):
    from ownpr_cell_timing import NativeCellTiming
    from native_boundary_cec import functions
    from ownpr_ff_configuration import FF_CONTROLS
    from audit_ownpr_native_export import audit
    placement, sta = Path(placement), Path(sta)
    read = load_json
    audit(sta, placement)
    meta = read(sta/'manifest.json'); design = read(placement/'packed.json'); placed = read(placement/'placement.json')
    if meta['packed_sha256'] != sha(placement/'packed.json') or meta['placement_sha256'] != sha(placement/'placement.json'):
        raise ValueError('Arrival graph placement differs from audited STA')
    cells = design['modules']['top']['cells']; aliases = read(sta/'name_map.json')['instances']; reverse = {a:n for n,a in aliases.items()}
    captures = read(sta/'sta_endpoint_audit.json'); points = {(p['instance'],p['pin']):p for p in captures}
    paths = report_paths(read_artifact_text(sta/'probe.tr'))
    if len(paths) != read(sta/'handoff_audit.json')['timing']['path_coverage']['setup']['paths']:
        raise ValueError('Truncated setup evidence')
    # IO envelope remains an observed-mode assumption, explicitly not an ABI query.
    io_values = collections.defaultdict(set); input_bounds = {}; clock_inputs = set()
    for path in paths:
        for arrival, delay, kind, transition, _, _, label in path['rows']:
            alias, _, pin = label.rpartition('/'); name = reverse.get(alias)
            if name is None: continue
            if kind == 'tINS' and cells[name]['type'] in ('IBUF','OBUF'):
                io_values[cells[name]['type'],transition[-1]].add(float(delay))
            if kind == 'tIn': input_bounds[name] = float(arrival)
            if kind == 'tCL': clock_inputs.add(name)
    if len(clock_inputs) != 1: raise ValueError('Arrival feedback requires one reported primary clock')
    clock_name = next(iter(clock_inputs)); clock_bit = cells[clock_name]['connections']['O'][0]
    io_envelope = {};io_mode_delays={};io_fallbacks=[]
    for typ in ('IBUF','OBUF'):
        observed = [v for (t,tr), vals in io_values.items() if t == typ for v in vals]
        if not observed: raise ValueError('Missing observed IO delay envelope')
        io_envelope[typ] = max(observed)
        io_mode_delays[typ]=[]
        for tr in ('R','F'):
            values=io_values.get((typ,tr))
            if not values:io_fallbacks.append(dict(primitive=typ,transition=tr))
            io_mode_delays[typ].append(max(values) if values else io_envelope[typ])
    clock_delay = max(io_values['IBUF','R'])
    # This first adapter accepts only the existing complete zero-delay IO contract.
    sdc = (sta/'probe.sdc').read_text()
    bounds = re.findall(r'^set_input_delay -(?:max|min) ([-+\d.eE]+) ', sdc, re.M)
    if len(bounds)!=len(re.findall(r'^set_input_delay\b',sdc,re.M)) or not bounds or any(float(x) != 0 for x in bounds) or re.search(r'^set_(?:false_path|multicycle_path|clock_groups|case_analysis)',sdc,re.M):
        raise ValueError('Unsupported input delay/exception contract for arrival feedback')
    if set(re.findall(r'IO_TYPE=(\w+)',(sta/'project.cst').read_text()))!={'LVCMOS33'}:
        raise ValueError('IO envelope currently supports only the audited LVCMOS33 mode')
    raw = NativeCellTiming(); ffgroup = raw.data['groups'][raw.data['id_map'][2]]
    c2q = ffgroup['rows'][ffgroup['pin_ids'][24]][6:8]
    io_c2q=ioreg_clock_to_q(read(sta/'ioreg_timing_queries.json'),placed,aliases) if placed.get('ioreg_clusters') else {}
    ext = read(sta/'extended_timing_queries.json') if available(sta/'extended_timing_queries.json') else dict(alu_cells=[],carry_links=[])
    mux = read(sta/'mux_timing_queries.json') if available(sta/'mux_timing_queries.json') else dict(muxes=[])
    if any(c['type']=='ALU' for c in cells.values()) and not ext['alu_cells']:raise ValueError('Missing arithmetic timing queries')
    if any(c['type'].startswith('MUX2_LUT') for c in cells.values()) and not mux['muxes']:raise ValueError('Missing dedicated mux timing queries')
    alu_queries = {(reverse[r['instance']],q['source'],q['destination']):q['values'][2:4]
        for r in ext['alu_cells'] for q in r['queries'] if q['selector']==1}
    mux_queries = {(reverse[r['instance']],q['pin']):q['values'][2:4]
        for r in mux['muxes'] for q in r['queries'] if q['selector']==1}
    dedicated = {(reverse[r['destination']],'CIN'):r['maximum_ns'] for r in ext['carry_links']}
    dedicated.update({(reverse[r['instance']],q['pin']):q['maximum_ns'] for r in mux['muxes'] for q in r['links']})
    dedicated.update({(r['iobuf'],'I'):0. for r in placed.get('ioreg_clusters',{}).values()})
    for r in placed['clusters'].values():
        if r['ff'] and r['lut']: dedicated[r['ff'],'D'] = 0.
    drivers = {}; constants = {'0':0, '1':1}
    for n,c in cells.items():
        for pin,bit in timing_pins(c,'output'):
            if bit in drivers and c['type'] not in ('VCC','GND'): raise ValueError('Multiple timing drivers')
            drivers[bit] = n,pin
            if c['type'] in ('VCC','GND'): constants[bit]=int(c['type']=='VCC')
    hard_arcs={};hard_info=None
    if placed.get('hard_clusters'):
        from ownpr_hard_arrival_edges import load as load_hard
        hard_arcs,hard_info=load_hard(sta,cells,reverse,clock_bit,constants)
        if set(hard_arcs)!=set(placed['hard_clusters']):raise ValueError('Hard arrival coverage differs from placement')
    nodes = set(); edges = []; seeds = {}; labels = {}
    vertex = lambda n,p: n+'/'+p
    def add(source, dest, delay, transitions, kind):
        nodes.update((source,dest)); edges.append(dict(source=source,destination=dest,delay=list(delay),transitions=transitions,kind=kind))
    for n,c in cells.items():
        typ=c['type']
        if typ in ('GND','VCC','GSR'): continue
        if typ=='IBUF':
            nodes.add(vertex(n,'O')); seeds[vertex(n,'O')]=[clock_delay]*2 if n==clock_name else io_mode_delays['IBUF']
            continue
        if typ in FF_CONTROLS:
            if c['connections']['CLK'] != [clock_bit]: raise ValueError('Unsupported FF/clock domain')
            clock=points[n,'CLK']['nodes']
            if len(clock)!=1: raise ValueError('Missing FF clock query')
            seed=[clock_delay+clock[0]['wire_delays'][1][2]+v for v in io_c2q.get(n,c2q)]
            nodes.add(vertex(n,'Q'));seeds[vertex(n,'Q')]=seed
        for pin,bit in timing_pins(c,'input'):
            if n in hard_arcs and pin not in hard_info['timed_input_pins'][n]:continue
            if pin=='CLK' or bit in constants:continue
            if bit not in drivers: raise ValueError('Undriven data timing pin: '+vertex(n,pin))
            source=drivers[bit]; capture=points.get((n,pin))
            if capture is None and (n,pin) in dedicated: delay=[dedicated[n,pin]]*2
            elif capture is None: raise ValueError('Missing timing endpoint query: '+vertex(n,pin))
            elif len(capture['nodes'])==1: delay=capture['nodes'][0]['wire_delays'][1][2:4]
            elif not capture['nodes'] and (n,pin) in dedicated: delay=[dedicated[n,pin]]*2
            else: raise ValueError('Ambiguous/unmodelled dedicated data edge')
            add(vertex(*source),vertex(n,pin),delay,[(0,0),(1,1)],'wire')
        if typ in FF_CONTROLS: continue
        if n in hard_arcs:
            for arc in hard_arcs[n]:add(arc['source'],arc['destination'],arc['delay'],arc['transitions'],arc['kind'])
            continue
        if typ=='OBUF':
            add(vertex(n,'I'),vertex(n,'O'),io_mode_delays['OBUF'],[(0,0),(1,1)],'io_observed_mode'); continue
        funcs=functions(c)
        if not funcs: raise ValueError('Unsupported combinational primitive '+typ)
        for inputs,output,truth in funcs:
            output_pin=next(p for p,b in c['connections'].items() if c['port_directions'][p]=='output' and b==[output])
            pins=(['I'+str(i) for i in range(len(inputs))] if typ.startswith('LUT') else
                  ['I0','I1','I3','CIN'] if typ=='ALU' else ['I0','I1','S0'])
            functional=senses(truth,len(inputs),{i:constants[b] for i,b in enumerate(inputs) if b in constants})
            # Observed native LUT STA arcs are non-unate even for unused INIT
            # variables. Retain these for agreement; expose functional support
            # separately so synthesis can remove the actual unused connection.
            # Native mode-2 carry STA is also non-unate: e.g. I1->COUT
            # reports RF even when the Boolean carry function is positive.
            # Keep functional senses separate; do not infer timing pruning.
            calibrated_nonunate_alu=typ=='ALU' and int(c['parameters']['ALU_MODE'],2)==2
            timing_senses=({i:[(0,0),(0,1),(1,0),(1,1)] for i,b in enumerate(inputs) if b not in constants}
                          if typ.startswith('LUT') or calibrated_nonunate_alu else functional)
            for index,transitions in timing_senses.items():
                pin=pins[index]
                if typ.startswith('LUT'):delay=raw.lut_input(index,placed['clusters'][n]['slot'],1)[2:4]
                elif typ=='ALU':delay=alu_queries[n,pin,output_pin]
                else:delay=mux_queries[n,pin]
                add(vertex(n,pin),vertex(n,output_pin),delay,transitions,'cell')
                edges[-1]['functional_transitions']=functional.get(index,[])
    for path in paths:
        row=path['rows'][-1]; alias,_,pin=row[6].rpartition('/'); n=reverse[alias]
        key=vertex(n,pin),int(row[3][-1]=='F')
        if key in labels:raise ValueError('Duplicate endpoint transition requires a broader launch-domain model')
        labels[key]=dict(path=path['path'],reported_arrival_ns=path['Data Arrival Time'],reported_slack_ns=path['Slack'])
    return dict(nodes=sorted(nodes),edges=edges,seeds=seeds), labels, dict(
        source_sha256={str(p.resolve()):sha(p) for p in (placement/'packed.json',placement/'placement.json',sta/'manifest.json',sta/'handoff_audit.json',sta/'probe.tr',sta/'probe.sdc',sta/'project.cst',sta/'sta_endpoint_audit.json',sta/'extended_timing_queries.json',sta/'mux_timing_queries.json',sta/'ioreg_timing_queries.json') if available(p)},
        raw_cell_library_sha256=raw.sha256,io_envelope_ns=io_envelope,io_mode_delays_ns=io_mode_delays,io_envelope_fallbacks=io_fallbacks,
        scope='Slow maximum arrivals, single positive clock and zero input delays; observed IO envelopes, no hold/exception/general primitive signoff',
        hard_arrival_model=hard_info,io_register_clock_to_q_ns=io_c2q,io_envelope_independently_queried=False,timing_signoff=False)


def analyze(placement,sta,out):
    from workspace_budget import reserve
    from ownpr_device import ROOT
    reserve(ROOT,5_000_000);start=time.monotonic();graph,labels,info=build(placement,sta)
    propagation_start=time.monotonic();values,parents,competitors=propagate(**graph);propagation_seconds=time.monotonic()-propagation_start;rows=[]
    for (node,tr),reference in labels.items():
        value=values.get(node,[-math.inf]*2)[tr]
        rows.append(dict(endpoint=node,transition='RF'[tr],**reference,predicted_arrival_ns=value,
            difference_ns=value-reference['reported_arrival_ns']))
    if any(not math.isfinite(r['predicted_arrival_ns']) for r in rows):raise ValueError('Uncovered reported setup endpoint')
    info.update(status='arrival_feedback_evaluated',vertices=len(graph['nodes']),edges=len(graph['edges']),
        boundaries=len(graph['seeds']),reported_endpoints=rows,maximum_absolute_difference_ns=max(abs(r['difference_ns']) for r in rows),
        wall_seconds=time.monotonic()-start,propagation_seconds=propagation_seconds,model_source_sha256=sha(Path(__file__)),
        lut_timing_policy='Observed native non-unate arcs including functionally unused inputs; independent functional support retained',
        alu_timing_policy='Mode-2 non-unate timing arcs, independently retained Boolean senses; other modes not promoted')
    payloads={'graph.json':json.dumps(graph,indent=2),
        'arrivals.json':json.dumps({k:[v if math.isfinite(v) else None for v in a] for k,a in values.items()},indent=2),
        'competitors.json':json.dumps(competitors,indent=2)}
    info['derived_artifact_sha256']={name:hashlib.sha256(text.encode('utf-8')).hexdigest() for name,text in payloads.items()}
    payloads['manifest.json']=json.dumps(info,indent=2)
    reserve(ROOT,sum(len(text.encode('utf-8')) for text in payloads.values())+1_000_000)
    out=Path(out);out.mkdir(exist_ok=False)
    (out/'model_source.py.txt').write_bytes(Path(__file__).read_bytes())
    for name,text in payloads.items():(out/name).write_bytes(text.encode('utf-8'))
    return info


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--placement',type=Path,required=True);p.add_argument('--sta',type=Path,required=True);p.add_argument('--out',type=Path,required=True);a=p.parse_args()
    r=analyze(a.placement,a.sta,a.out);print(json.dumps({k:r[k] for k in ('status','vertices','edges','boundaries','maximum_absolute_difference_ns','wall_seconds')},indent=2))
