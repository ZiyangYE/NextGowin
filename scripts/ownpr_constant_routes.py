"""Resolve and audit native ownership of routed VSS/VCC constant forests."""
import json,re
from pathlib import Path


def connection_bit(cell,label):
    """Resolve a scalar native pin label without silently truncating a bus."""
    values=cell['connections'].get(label)
    if values is not None:
        if len(values)!=1:raise ValueError('Native scalar label refers to an unindexed bus')
        return values[0]
    match=re.fullmatch(r'(\w+)\[(\d+)\]',label)
    if match:
        values=cell['connections'].get(match[1]);index=int(match[2])
        if values is not None and len(values)>1 and index<len(values):return values[index]
    raise ValueError('Native pin label does not identify a logical bit')


def source_check(name,row,tie_ids):
    if name not in ('constant_0','constant_1') or row.get('clock'):raise ValueError('Unsupported constant route identity')
    value=int(name[-1]);sources={tuple(p) for p in row['sources']};roots={tuple(p['node']) for p in row['parents'] if p['parent'] is None}
    if not sources or roots!=sources or len(sources)!=len(row['sources']):raise ValueError('Constant forest roots differ from tie sources')
    if any(len(p)!=3 or p[0]<1 or p[1]<1 or p[2]!=tie_ids[value] for p in sources):raise ValueError('Constant route has the wrong physical tie polarity')
    return value,sorted(sources)


def owner(name,row,nets,constant_pointers,tie_ids):
    value,sources=source_check(name,row,tie_ids);pointers=set(constant_pointers.get(value,[]))
    if len(pointers)!=1 or not next(iter(pointers)):raise ValueError('Constant route lacks one native logical owner')
    if pointers&set(constant_pointers.get(1-value,[])):raise ValueError('Opposite constant polarities share a logical owner')
    pointer=next(iter(pointers));names=sorted(n for n,p in nets.items() if p==pointer)
    if not names:raise ValueError('Constant owner is absent from native logical nets')
    return pointer,dict(route=name,value=value,physical_sources=[list(p) for p in sources],native_net_names=names)


def audit(folder,design,device):
    from ownpr_image_storage import load_json
    folder=Path(folder);read=load_json;expanded=read(folder/'expanded_routes.json')['nets']
    rows={n:r for n,r in expanded.items() if n.startswith('constant_')};bindings=read(folder/'binding_report.json').get('constant_routes',[])
    if not rows:
        if bindings:raise ValueError('Unexpected constant route binding')
        return None
    if len(bindings)!=len(rows) or {r['route'] for r in bindings}!=set(rows):raise ValueError('Incomplete constant route ownership capture')
    cells=design['modules']['top']['cells'];constants={'0':0,'1':1};native_names={0:set(),1:set()}
    for c in cells.values():
        if c['type'] in ('GND','VCC'):constants[next(iter(c['connections'].values()))[0]]=int(c['type']=='VCC')
    connectivity=read(folder/'native_connectivity.json')
    for c in connectivity:
        typ=cells[c['instance']]['type']
        if typ in ('GND','VCC') and c['direction']=='Out':native_names[int(typ=='VCC')].add(c['net'])
    if native_names[0]&native_names[1]:raise ValueError('Native constant polarities share a net')
    captures=read(folder/'sta_endpoint_audit.json');checked=[];pin_net={(r['instance'],r['pin']):r['net'] for r in connectivity}
    for record in bindings:
        name=record['route'];row=rows[name];value,sources=source_check(name,row,{0:device.ids['VSS'],1:device.ids['VCC']})
        if record['value']!=value or record['physical_sources']!=[list(s) for s in sources] or set(record['native_net_names'])!=native_names[value]:raise ValueError('Constant ownership disagrees with physical or logical sources')
        endpoints=[]
        for sink in row['sinks']:
            matches=[r for r in captures if r['direction']=='In' and any(n['node']==sink['node'] for n in r['nodes'])]
            if len(matches)!=1:raise ValueError('Ambiguous constant physical sink')
            r=matches[0];bit=connection_bit(cells[r['instance']],r['pin'])
            if constants.get(bit)!=value or pin_net[r['instance'],r['pin']] not in native_names[value]:raise ValueError('Constant sink logical value or owner changed')
            node=next(n for n in r['nodes'] if n['node']==sink['node'])
            if node['chain']!=sink['path_sink_to_source'] or tuple(node['chain'][-1]) not in sources:raise ValueError('Native constant sink does not reach its physical tie source')
            endpoints.append(dict(instance=r['instance'],pin=r['pin'],node=sink['node'],source=node['chain'][-1]))
        checked.append(dict(route=name,value=value,sources=len(sources),sinks=endpoints))
    return dict(status='constant_route_ownership_and_native_paths_matched',networks=checked,
        scope='Ordinary routed VSS/VCC forests; static FF/ALU/IO control modes remain separately audited',full_bitstream_verified=False)
