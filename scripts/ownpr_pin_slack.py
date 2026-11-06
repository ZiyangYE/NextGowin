"""Conservative destination hold/removal budgets for local LUT-pin ECO."""
import re
from collections import defaultdict
from ownpr_ff_configuration import FF_CONTROLS


def downstream_budgets(design,mapping,report):
    cells=design['modules']['top']['cells'];loads=defaultdict(list);slacks={}
    for kind,index in (('Hold',2),('Removal',4)):
        section=re.search(r'^3\.1\.'+str(index)+' '+kind+r' Paths Table\n(.*?)(?=\n3\.)',report,re.M|re.S)
        if not section:raise ValueError('Missing minimum-delay endpoint table')
        for slack,endpoint in re.findall(r'^\s*\d+\s+([-\d.]+)\s+\S+\s+(own_i_\d+/\w+)\s+',section[1],re.M):
            slacks[endpoint]=min(slacks.get(endpoint,float('inf')),float(slack))
    for n,c in cells.items():
        for p,bits in c['connections'].items():
            if c['port_directions'][p]=='input':
                for b in bits:loads[b].append((n,p))
    def budget(name,pin):
        pending=[(name,pin)];seen=set();endpoints=set()
        while pending:
            n,p=pending.pop()
            if (n,p) in seen:continue
            seen.add((n,p));cell=cells[n];alias=mapping['instances'][n]
            if cell['type'] in FF_CONTROLS:
                if p=='CLK':raise ValueError('Pin ECO touches a clock cone')
                endpoints.add(alias+'/'+p);continue
            if cell['type']=='OBUF':endpoints.add(alias+'/O');continue
            if cell['type'] not in ('LUT1','LUT2','LUT3','LUT4','ALU','MUX2_LUT5','MUX2_LUT6','MUX2_LUT7'):raise ValueError('Uncovered timing cone primitive')
            for op,bits in cell['connections'].items():
                if cell['port_directions'][op]=='output':
                    for b in bits:pending.extend(loads[b])
        if not endpoints or not endpoints<=slacks.keys():raise ValueError('Pin ECO reaches an uncovered minimum-delay endpoint')
        return min(slacks[e] for e in endpoints)
    return budget
