"""Augment reported-path candidates with validated worst-through-pin slacks.

Pin observations expose alternate paths hidden by one-path-per-endpoint
reports. They are not complete path witnesses or per-transition arrivals.
"""
import copy,re


def augment(ranking,design,nets,budgets):
    result=copy.deepcopy(ranking);cells=design['modules']['top']['cells']
    ordinary={n['name']:n for n in nets if not n.get('clock') and n.get('constant_value') is None}
    drivers={b:dict(instance=n,primitive=c['type'],pin=p) for n,c in cells.items()
        for p,bs in c['connections'].items() if c['port_directions'][p]=='output' for b in bs}
    rows={r['net']:r for r in result['ranked_nets']}
    witnessed={}
    for path in result['traces']:
        for arc in path['ordinary_wires']:
            key=arc['net'],arc['endpoint'];witnessed[key]=min(witnessed.get(key,float('inf')),path['slack_ns'])
    for row in rows.values():
        row['receiver_slacks']={label:witnessed[row['net'],label] for label in row['endpoints']}
        row['receiver_slack_sources']={label:'reported_path_witness' for label in row['endpoints']}
    added=0
    for (name,pin),budget in budgets.items():
        if name not in cells:raise ValueError('Pin feedback refers to an unknown cell')
        match=re.fullmatch(r'(\w+)(?:\[(\d+)\])?',pin)
        if not match:raise ValueError('Unsupported pin feedback label')
        port=match[1];index=int(match[2] or 0);cell=cells[name]
        if cell['port_directions'].get(port)!='input' or port in ('CLK','CLKA','CLKB','CIN'):continue
        if index>=len(cell['connections'][port]):raise ValueError('Pin feedback index outside port')
        bit=cell['connections'][port][index];net='net_'+str(bit)
        if net not in ordinary or bit not in drivers:continue
        label=name+'/'+pin;slack=budget['original_setup_slack_ns']
        row=rows.setdefault(net,dict(net=net,driver=drivers[bit],fanout=len(ordinary[net]['sinks']),path_occurrences=0,
            maximum_reported_wire_ns=0.,total_reported_wire_ns=0.,worst_path_slack_ns=slack,endpoints=[],receiver_slacks={},receiver_slack_sources={}))
        if label not in row['endpoints']:row['endpoints'].append(label);added+=1
        row['receiver_slacks'][label]=min(row['receiver_slacks'].get(label,float('inf')),slack)
        row['receiver_slack_sources'][label]='validated_native_worst_through_pin'
    for row in rows.values():
        row['worst_path_slack_ns']=min(row['receiver_slacks'].values())
    # Native float queries differ by a few femtoseconds along one path.
    # Rank at the report's 1 ps resolution; preserve raw values for budgets.
    result['ranked_nets']=sorted(rows.values(),key=lambda r:(round(r['worst_path_slack_ns'],3),-r['maximum_reported_wire_ns'],-r['total_reported_wire_ns'],r['net']))
    result['receiver_ranking']=dict(additional_receivers=added,ranking_resolution_ns=.001,scope='Aggregate receiver slack; original path traces retained without fabricating alternate paths',full_design_sta_required=True)
    return result
