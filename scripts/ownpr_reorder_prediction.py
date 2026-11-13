"""Rank wide-input reorderings by propagating competing input arrivals.

Transferred wire delays are observed samples, not routing bounds. Reported
endpoint transitions anchor the comparison; full routed native STA is required.
"""
import copy,json,math,time
from pathlib import Path
from ownpr_arrival_graph import build,propagate,senses
from ownpr_cell_timing import NativeCellTiming
from ownpr_mux_macros import tree
from reorder_ownpr_wide_inputs import reorder
from ownpr_place import sha
from ownpr_timing_numbers import negative_slack


def project(graph,before,after,placed,root,library,policy='max_observed'):
    if policy not in ('max_observed','min_observed'):raise ValueError('Unknown wire-transfer scenario')
    old=before['modules']['top']['cells'];new=after['modules']['top']['cells'];spec=tree(before,root)
    members=set(spec['leaves'])|set(spec['muxes']);instance=lambda node:node.rsplit('/',1)[0]
    if set(old)!=set(new) or before['modules']['top']['ports']!=after['modules']['top']['ports']:
        raise ValueError('Projection changed cell/port identity')
    for name,cell in old.items():
        if name not in members:
            if new[name]!=cell:raise ValueError('Projection changed another macro or state boundary')
        elif (cell['type']!=new[name]['type'] or cell['port_directions']!=new[name]['port_directions'] or
              any(new[name]['connections'][p]!=b for p,b in cell['connections'].items() if cell['port_directions'][p]=='output')):
            raise ValueError('Projection changed physical cell/output identity')
    if tree(after,root)!=spec:raise ValueError('Projection changed dedicated topology')
    constants={'0':0,'1':1};drivers={}
    for name,cell in old.items():
        for pin,bits in cell['connections'].items():
            if cell['port_directions'][pin]=='output':drivers[bits[0]]=name+'/'+pin
        if cell['type'] in ('GND','VCC'):constants[next(iter(cell['connections'].values()))[0]]=int(cell['type']=='VCC')
    wires={};cell_edges={};samples={}
    for edge in graph['edges']:
        if instance(edge['destination']) not in members:continue
        if edge['kind']=='wire':
            wires[edge['source'],edge['destination']]=edge
            if instance(edge['source']) not in members:samples.setdefault(edge['source'],[]).append(edge['delay'])
        elif edge['kind']=='cell':cell_edges[edge['source'],edge['destination']]=edge
    edges=[copy.deepcopy(e) for e in graph['edges'] if instance(e['destination']) not in members]
    nodes=set(graph['nodes']);transferred=[]
    def add(source,dest,delay,transitions,kind,functional=None):
        nodes.update((source,dest));e=dict(source=source,destination=dest,delay=list(delay),transitions=transitions,kind=kind)
        if functional is not None:e['functional_transitions']=functional
        edges.append(e)
    for name in sorted(members):
        cell=new[name];width=4 if cell['type']=='LUT4' else 3
        pins=['I0','I1','I2','I3'] if width==4 else ['I0','I1','S0'];output='F' if width==4 else 'O'
        truth=int(cell['parameters']['INIT'],2) if width==4 else 0xCA
        functional=senses(truth,width,{i:constants[cell['connections'][p][0]] for i,p in enumerate(pins) if cell['connections'][p][0] in constants})
        for i,pin in enumerate(pins):
            bit=cell['connections'][pin][0]
            if bit in constants:continue
            source=drivers[bit];dest=name+'/'+pin;old_edge=wires.get((source,dest))
            if old_edge is not None:delay=old_edge['delay']
            elif instance(source) in members:raise ValueError('Missing unchanged dedicated edge')
            else:
                donor=samples.get(source)
                if not donor:raise ValueError('No observed wire sample for new macro input')
                choose=max if policy=='max_observed' else min
                delay=[choose(d[k] for d in donor) for k in range(2)]
                transferred.append(dict(source=source,destination=dest,delay=delay,donor_count=len(donor)))
            add(source,dest,delay,[(0,0),(1,1)],'wire')
            if width==4:
                delay=library.lut_input(i,placed['clusters'][name]['slot'],1)[2:4];transitions=[(0,0),(0,1),(1,0),(1,1)]
            else:
                observed=cell_edges.get((dest,name+'/O'))
                if observed is None:raise ValueError('Missing calibrated dedicated mux arc')
                delay=observed['delay'];transitions=functional.get(i,[])
            add(dest,name+'/'+output,delay,transitions,'cell',functional.get(i,[]))
    return dict(nodes=sorted(nodes),edges=edges,seeds=copy.deepcopy(graph['seeds'])),transferred


def projected_metrics(labels,baseline,changed):
    rows=[]
    for (node,tr),reference in labels.items():
        if not math.isfinite(changed[node][tr]) or not math.isfinite(baseline[node][tr]):raise ValueError('Projected endpoint lost its timing boundary')
        slack=reference['reported_slack_ns']-(changed[node][tr]-baseline[node][tr])
        rows.append(dict(endpoint=node,transition='RF'[tr],slack_ns=slack))
    return dict(setup_wns_ns=min(r['slack_ns'] for r in rows),setup_violations=sum(negative_slack(r['slack_ns']) for r in rows),
        total_negative_setup_slack_ns=sum(min(0.,r['slack_ns']) for r in rows),worst_endpoint=min(rows,key=lambda r:r['slack_ns']))


def apply_routed_wires(graph,design,placed,base_sta,routes,wire,placement):
    """Use measured complete new trees, including branches outside the rewrite."""
    from audit_ownpr_joint_routes import audit as joint_audit
    base_sta,routes,wire,placement=map(Path,(base_sta,routes,wire,placement));read=lambda p:json.loads(Path(p).read_text())
    meta=read(wire/'manifest.json');data=read(wire/'wire_delays.json');route_meta=read(routes/'manifest.json');base=read(base_sta/'manifest.json')
    if (meta['status']!='measured' or meta['dll_sha256']!=base['dll_sha256'] or meta['wire_delays_sha256']!=sha(wire/'wire_delays.json') or
        data['source_routes_sha256']!=sha(routes/'routes.json') or data['source_nets_sha256']!=sha(routes/'nets.json') or
        route_meta['netlist_sha256']!=sha(placement/'packed.json') or route_meta['placement_sha256']!=base['placement_sha256'] or
        sha(routes/'clock_tree.json')!=base['clock_tree_sha256']):raise ValueError('Routed arrival feedback has mismatched data, geometry or clock')
    joint_audit(placement,routes,wire)
    points={(r['instance'],r['pin']):r for r in read(base_sta/'sta_endpoint_audit.json')}
    cells=design['modules']['top']['cells'];paired={r['ff']:r['lut'] for r in placed['clusters'].values() if r['ff'] and r['lut']}
    ioreg={r['iobuf']:n for n,r in placed.get('ioreg_clusters',{}).items()};changed=copy.deepcopy(graph);records=[];count=0;device=None;restored=[]
    for e in changed['edges']:
        if e['kind']!='wire':continue
        name,pin=e['destination'].rsplit('/',1);source,source_pin=e['source'].rsplit('/',1)
        c=cells[name];bit=c['connections'][pin][0];net='net_'+str(bit)
        dedicated=(c['type'].startswith('MUX2_LUT') and pin in ('I0','I1') or
            c['type']=='ALU' and pin=='CIN' and cells[source]['type']=='ALU' and source_pin=='COUT' or
            pin=='D' and paired.get(name)==source and source_pin=='F' or
            pin=='I' and ioreg.get(name)==source and source_pin=='Q')
        if dedicated:continue
        if net not in data['nets']:raise ValueError('Missing measured ordinary input net: '+net+' '+e['destination'])
        capture=points.get((name,pin))
        if capture is not None and len(capture['nodes'])==1:point=capture['nodes'][0]['node']
        elif c['type']=='LUT4' and name in placed['clusters'] and pin in ('I0','I1','I2','I3'):
            # Native reports omit constant input pins. Reordering can reactivate
            # them at the same calibrated CFU, so recover their physical binding.
            if device is None:
                from ownpr_device import Device
                device=Device()
            loc=placed['clusters'][name]
            if device.grid[loc['row']][loc['column']]!=17:raise ValueError('Uncalibrated reactivated macro input')
            point=[loc['row']+1,loc['column']+1,device.ids['ABCD'[int(pin[1])]+str(loc['slot'])]]
            restored.append(dict(endpoint=e['destination'],node=point))
        else:raise ValueError('Missing unchanged physical input coordinate: '+e['destination'])
        matches=[r for r in data['nets'][net]['sinks'] if r['node']==point]
        if len(matches)!=1:raise ValueError('Measured route does not cover candidate input')
        delay=matches[0]['delays_by_selector'][1][2:4];count+=1
        if any(abs(a-b)>1e-6 for a,b in zip(delay,e['delay'])):records.append(dict(net=net,endpoint=e['destination'],before=e['delay'],after=delay))
        e['delay']=delay
    return changed,dict(measured_wire_edges=count,changed_edges=records,reactivated_macro_pins=restored,
        source_sha256={str(p.resolve()):sha(p) for p in (placement/'packed.json',placement/'placement.json',routes/'routes.json',routes/'clock_tree.json',wire/'wire_delays.json',wire/'manifest.json')})


def assess_routed(base_placement,base_sta,placement,routes,wire,root):
    read=lambda p:json.loads(Path(p).read_text());graph,labels,info=build(base_placement,base_sta);before_values,_,_=propagate(**graph)
    before=read(Path(base_placement)/'packed.json');after=read(Path(placement)/'packed.json');placed=read(Path(base_placement)/'placement.json')
    projected,_=project(graph,before,after,placed,root,NativeCellTiming())
    measured,changes=apply_routed_wires(projected,after,placed,base_sta,routes,wire,placement);values,_,_=propagate(**measured)
    spec=tree(before,root);members=set(spec['leaves'])|set(spec['muxes'])
    changes['changed_outside_macro']=[r for r in changes['changed_edges'] if r['endpoint'].rsplit('/',1)[0] not in members]
    return dict(status='routed_arrivals_propagated',metrics=projected_metrics(labels,before_values,values),wire_measurements=changes,
        source_sha256=info['source_sha256'],timing_signoff=False,
        scope='New complete routed-tree delays with baseline clock/IO boundaries; reported endpoint transitions, no hold or general signoff')


def rank(placement,sta,candidates,seconds=60):
    start=time.monotonic();deadline=start+seconds;placement=Path(placement)
    graph,labels,info=build(placement,sta);baseline,_,_=propagate(**graph)
    error=max(abs(baseline[n][tr]-r['reported_arrival_ns']) for (n,tr),r in labels.items())
    if error>.002:raise ValueError('Arrival projection baseline differs from native report by more than 2 ps')
    before=json.loads((placement/'packed.json').read_text());placed=json.loads((placement/'placement.json').read_text());library=NativeCellTiming()
    ranked=[];rejected=[];before_metrics=projected_metrics(labels,baseline,baseline)
    for candidate in candidates:
        if candidate['kind']!='wide_reorder':continue
        if time.monotonic()>=deadline:break
        try:
            after,_=reorder(before,candidate['root'],candidate['late_bit'],candidate.get('target_index'));scenarios={}
            for policy in ('max_observed','min_observed'):
                projected,transfers=project(graph,before,after,placed,candidate['root'],library,policy)
                values,_,_=propagate(**projected)
                scenarios[policy]=dict(metrics=projected_metrics(labels,baseline,values),transferred_wires=transfers)
        except ValueError as error_detail:
            rejected.append(dict(key=candidate['key'],reason=str(error_detail)));continue
        ranked.append(dict(candidate,arrival_projection=scenarios))
    def score(row):
        m=row['arrival_projection']['max_observed']['metrics']
        return (-m['setup_wns_ns'],m['setup_violations'],-m['total_negative_setup_slack_ns'],row['key'])
    ranked.sort(key=score)
    return ranked,dict(baseline=before_metrics,native_arrival_max_error_ns=error,ranked_candidates=len(ranked),rejected=rejected,
        seconds=time.monotonic()-start,budget_seconds=seconds,budget_exhausted=time.monotonic()>=deadline,source_sha256=info['source_sha256'],
        scope='Full combinational propagation at reported endpoint transitions; unchanged external wires and transferred in-macro samples are predictions, not delay bounds or final STA',
        io_envelope_fallbacks=info['io_envelope_fallbacks'],timing_signoff=False,
        model_source_sha256={p.name:sha(p) for p in (Path(__file__),Path(__file__).with_name('ownpr_arrival_graph.py'),Path(__file__).with_name('reorder_ownpr_wide_inputs.py'))})
