"""Conservative endpoint-slack bounds through ordinary combinational cones.

These bounds cover structural fanout, including reconvergence and unreported
alternate inputs. They are wire-only estimates, not propagated pin arrival /
required times: changed slew and cell loading still require full final STA.
Unknown cells, cycles, dangling branches and missing endpoint rows fall back.
"""
from collections import defaultdict
from ownpr_ff_configuration import FF_CONTROLS

COMBINATIONAL={'LUT1','LUT2','LUT3','LUT4','MUX2_LUT5','MUX2_LUT6','MUX2_LUT7','MUX2_LUT8'}


def receiving_cones(design, captures):
    cells=design['modules']['top']['cells'];aliases={};sinks=defaultdict(set)
    for row in captures:
        name=row['instance'];alias=row['native_name']
        if name not in cells or (name in aliases and aliases[name]!=alias):
            raise ValueError('Inconsistent cone endpoint mapping')
        aliases[name]=alias
    for name,cell in cells.items():
        for port,bits in cell['connections'].items():
            if cell['port_directions'].get(port) in ('input','inout'):
                for bit in bits:
                    if isinstance(bit,int):sinks[bit].add((name,port))
    cache={};visiting=set()
    def visit(key):
        if key in cache:return cache[key]
        if key in visiting:return None
        name,pin=key;cell=cells[name];typ=cell['type'];answer=None
        if name not in aliases:return None
        if typ in FF_CONTROLS and pin in ('D','CE'):
            answer=frozenset((aliases[name]+'/'+pin,))
        elif typ=='OBUF' and pin=='I':answer=frozenset((aliases[name]+'/O',))
        elif typ in COMBINATIONAL and cell['port_directions'].get(pin)=='input':
            visiting.add(key);endpoints=set();complete=True
            outputs=[bit for port,bits in cell['connections'].items() if cell['port_directions'].get(port)=='output' for bit in bits]
            if not outputs:complete=False
            for bit in outputs:
                receivers=sinks.get(bit,set())
                if not receivers:complete=False
                for receiver in sorted(receivers):
                    reached=visit(receiver)
                    if reached is None:complete=False
                    else:endpoints.update(reached)
            visiting.remove(key)
            if complete and endpoints:answer=frozenset(endpoints)
        cache[key]=answer
        return answer
    for row in captures:
        if row['direction']=='In':visit((row['instance'],row['pin']))
    return cache


def physical_budgets(graph, design, captures, tables):
    cones=receiving_cones(design,captures);by_node=defaultdict(list)
    for row in captures:
        if row['direction']!='In' or len(row['nodes'])!=1:continue
        r,c,i=row['nodes'][0]['node']
        if min(r,c)<1 or i not in graph.device.names:continue
        node=graph.node(r-1,c-1,graph.device.names[i])
        by_node[node].append(((row['instance'],row['pin']),cones.get((row['instance'],row['pin']))))
    budgets={}
    for node,entries in by_node.items():
        if any(cone is None for _,cone in entries):continue
        endpoints=set().union(*(cone for _,cone in entries))
        if not endpoints or any(e not in tables['Setup'] or e not in tables['Hold'] for e in endpoints):continue
        setup=min(tables['Setup'][e] for e in endpoints);hold=min(tables['Hold'][e] for e in endpoints)
        # A failing baseline has no safe slack to spend; do not convert it to
        # a nonnegative "budget" that could hide the existing failure.
        budgets[node]=dict(setup=max(0.,setup-.001),hold=max(0.,hold-.01),
            original_setup_slack_ns=setup,original_hold_slack_ns=hold,
            endpoints=sorted(endpoints),receivers=[list(key) for key,_ in entries],
            scope='structural_downstream_endpoint_slack',full_design_sta_required=True)
    return budgets
