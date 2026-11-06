"""Transport historical pin priorities through physical/structural changes.

These are search hints, NOT valid slack budgets for the modified circuit.
Unchanged primitive semantics and the same logical input bit are required to
reuse a priority. New/changed/unknown receivers are maximally critical.
"""
from collections import defaultdict
from ownpr_pin_feedback import criticalities


def protected_placement_cells(design,layout,budgets,setup_ns=.5,hold_ns=.15):
    import math
    if not math.isfinite(setup_ns) or not math.isfinite(hold_ns) or not 0<=setup_ns<=100 or not 0<=hold_ns<=2:raise ValueError('Invalid placement protection window')
    members={n for n in layout['clusters']}|{r['ff'] for r in layout['clusters'].values() if r.get('ff')}
    cells=design['modules']['top']['cells'];observations=defaultdict(list)
    for (name,pin),value in budgets.items():
        port=pin.split('[',1)[0]
        if name in members and port not in ('CLK','CLKA','CLKB') and port in cells[name]['port_directions']:
            observations[name].append(value)
    protected=[]
    for name,values in sorted(observations.items()):
        setup=min(v['original_setup_slack_ns'] for v in values);hold=min(v['original_hold_slack_ns'] for v in values)
        if setup<=setup_ns or hold<=hold_ns:protected.append(dict(instance=name,setup_slack_ns=setup,hold_slack_ns=hold))
    return {r['instance'] for r in protected},dict(setup_window_ns=setup_ns,hold_window_ns=hold_ns,protected=protected,
        scope='Historical near-critical cells cannot be displaced incidentally. The explicitly transformed root and its paired FF may move; full new STA is still required.')


def compatible_pin(old,new,name,pin):
    a=old.get(name);b=new.get(name)
    if a is None or b is None:return False
    if a['type']!=b['type'] or a.get('parameters',{})!=b.get('parameters',{}) or a['port_directions']!=b['port_directions']:return False
    import re
    m=re.fullmatch(r'(\w+)(?:\[(\d+)\])?',pin)
    if not m:return False
    port,index=m[1],int(m[2] or 0)
    if a['port_directions'].get(port)!='input':return False
    ab=a['connections'].get(port,[]);bb=b['connections'].get(port,[])
    return len(ab)==len(bb) and index<len(ab) and ab[index]==bb[index]


def transport(nets,receivers,old_design,new_design,budgets,window_ns=4.):
    old=old_design['modules']['top']['cells'];new=new_design['modules']['top']['cells']
    by_node=defaultdict(set)
    for r in receivers:by_node[r['net'],r['node']].add((r['instance'],r['pin']))
    data_nodes={(n['name'],s) for n in nets if not n.get('clock') and 'constant_value' not in n for s in n['sinks']}
    relevant={k:budgets[k] for point,keys in by_node.items() if point in data_nodes for k in keys if k in budgets and compatible_pin(old,new,*k)}
    weights=criticalities(relevant,window_ns)
    result=[];known=unknown=shared=0;observed=[]
    for net in nets:
        row=dict(net)
        if not net.get('clock') and 'constant_value' not in net:
            scores={}
            for node in net['sinks']:
                keys=by_node.get((net['name'],node),set());valid=bool(keys) and all(k in weights and compatible_pin(old,new,*k) for k in keys)
                value=max(weights[k] for k in keys) if valid else 1.
                scores[node]=value;known+=int(valid);unknown+=int(not valid);shared+=int(len(keys)>1)
                observed.append(dict(net=net['name'],node=node,receivers=sorted(keys),historical_priority_reused=valid,criticality=value))
            row['sink_criticalities']=scores;row['criticality']=max(scores.values(),default=0.)
        result.append(row)
    if not known:raise ValueError('No unchanged logical receivers with checked historical feedback')
    return result,dict(window_ns=window_ns,known_receivers=known,unknown_receivers=unknown,shared_receivers=shared,receivers=observed,
        source='Validated historical pin slack; current physical binding',unknown_criticality=1.,current_slacks_validated=False,
        scope='Search priorities only. Current loaded timing, hold and shared-path budget protection still require fresh analysis.')
