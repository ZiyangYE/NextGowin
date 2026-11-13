"""Hash-bound, explicitly selected native pin slacks for routed own designs.

Validated on the pinned GW5AST ABI and single-clock CPU capture. Values are
aggregate worst-through-pin setup/hold slacks, not per-transition arrivals.
Budgets constrain wire-only deltas; changed loading still needs complete STA.
"""
import math,re
from collections import defaultdict
from pathlib import Path
from ownpr_image_storage import load_json,read_text,sha

SENTINEL=1e10
def valid(value):return isinstance(value,(int,float)) and math.isfinite(value) and abs(value)<1e9

def logical_budgets(rows):
    result={};seen=set()
    for row in rows:
        key=(row['instance'],row['pin'])
        if key in seen:raise ValueError('Duplicate pin feedback')
        seen.add(key)
        setup,hold=row['selected_1'],row['selected_0']
        if not valid(setup) or not valid(hold):continue
        result[key]=dict(setup=max(0.,setup-.001),hold=max(0.,hold-.01),original_setup_slack_ns=setup,original_hold_slack_ns=hold,
            scope='native_worst_through_pin_slack',full_design_sta_required=True)
    return result

def load(folder,sta):
    folder,sta=Path(folder),Path(sta);m=load_json(folder/'manifest.json');data=load_json(folder/'pin_feedback.json')
    if m['status']!='captured' or m.get('control_without_queries') or m['vendor_placement_executed'] or m['vendor_routing_executed']:
        raise ValueError('Not a native query on own routed state')
    if m['capture_sha256']!=sha(folder/'pin_feedback.json'):raise ValueError('Pin capture hash changed')
    for name,h in m['input_sha256'].items():
        if sha(sta/name)!=h or sha(folder/name)!=h:raise ValueError('Pin feedback source differs: '+name)
    # Independently compare every reported terminal against the explicit mode.
    pins={r['native_name']+'/'+r['pin']:r for r in data['pins']};text=read_text(sta/'probe.tr');matches={}
    for i,kind,selector in ((1,'Setup','selected_1'),(2,'Hold','selected_0')):
        section=re.search(r'^3\.1\.'+str(i)+' '+kind+r' Paths Table\n(.*?)(?=\n3\.)',text,re.M|re.S)
        if not section:raise ValueError('Missing endpoint table')
        rows=re.findall(r'^\s*\d+\s+([-\d.]+)\s+\S+\s+(\S+)\s+',section[1],re.M)
        if not rows:raise ValueError('Empty endpoint table')
        for slack,pin in rows:
            value=pins.get(pin,{}).get(selector)
            if not valid(value) or abs(value-float(slack))>.0011:raise ValueError('Explicit pin mode does not match '+kind+' endpoint '+pin)
        matches[kind]=len(rows)
    return logical_budgets(data['pins']),dict(capture_sha256=m['capture_sha256'],endpoint_matches=matches,
        scope='Aggregate pin slack validated against complete endpoint tables; single-clock pinned ABI',per_transition_arrival_validated=False)

def physical_budgets(graph,captures,budgets):
    by_node=defaultdict(list)
    for row in captures:
        if row['direction']!='In' or len(row['nodes'])!=1:continue
        r,c,i=row['nodes'][0]['node']
        if min(r,c)<1 or i not in graph.device.names:continue
        node=graph.node(r-1,c-1,graph.device.names[i]);by_node[node].append((row['instance'],row['pin']))
    result={}
    for node,keys in by_node.items():
        if any(key not in budgets for key in keys):continue
        values=[budgets[k] for k in keys]
        result[node]={k:min(v[k] for v in values) for k in ('setup','hold','original_setup_slack_ns','original_hold_slack_ns')}
        result[node].update(scope='native_worst_through_pin_slack',receivers=[list(k) for k in keys],full_design_sta_required=True)
    return result


def criticalities(budgets,window_ns=4.):
    if not math.isfinite(window_ns) or not 0<window_ns<=100:raise ValueError('Invalid pin criticality window')
    if not budgets:raise ValueError('Empty pin criticality budget')
    worst=min(v['original_setup_slack_ns'] for v in budgets.values())
    return {k:max(0.,min(1.,1-(v['original_setup_slack_ns']-worst)/window_ns))**2 for k,v in budgets.items()}


def routing_priorities(nets,budgets,window_ns=4.):
    """Annotate search copies, preserving the canonical routing contract."""
    present={s:budgets[s] for n in nets if not n.get('clock') and 'constant_value' not in n for s in n['sinks'] if s in budgets}
    weights=criticalities(present,window_ns);result=[]
    for net in nets:
        row=dict(net)
        if not net.get('clock') and 'constant_value' not in net:
            row['sink_criticalities']={s:weights.get(s,net.get('criticality',.5)) for s in net['sinks']}
            row['criticality']=max(row['sink_criticalities'].values(),default=0.)
        result.append(row)
    return result,dict(window_ns=window_ns,known_sinks=len(present),unknown_sinks=sum(s not in present for n in nets if not n.get('clock') and 'constant_value' not in n for s in n['sinks']),
        scope='Setup criticality controls search order/cost; hold and shared path budgets require complete STA after routing')


def placement_priorities(design,budgets,window_ns=4.):
    cells=design['modules']['top']['cells'];selected={}
    for (name,pin),value in budgets.items():
        port=pin.split('[',1)[0]
        if name in cells and cells[name]['port_directions'].get(port)=='input' and port not in ('CLK','CLKA','CLKB','CIN'):
            selected[name,pin]=value
    result={}
    for (name,pin),value in criticalities(selected,window_ns).items():
        key=name,pin.split('[',1)[0];result[key]=max(result.get(key,0.),value)
    return result
