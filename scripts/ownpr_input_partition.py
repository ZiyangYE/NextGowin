"""Select a reported critical nonclock input whose sinks have calibrated sites."""
import re
from ownpr_hold_buffers import receiver_site

def select_input(design,placed,names,text,min_sinks=4):
    cells=design['modules']['top']['cells'];reverse={v:k for k,v in names.items()}
    section=re.search(r'^3\.1\.1 Setup Paths Table\n(.*?)(?=\n3\.)',text,re.S|re.M)
    if not section:raise ValueError('Missing setup path table')
    candidates=[];seen=set()
    rows=re.findall(r'^\s*\d+\s+([-\d.]+)\s+(own_i_\d+)/\S+\s+',section[1],re.M)
    for slack,alias in sorted(rows,key=lambda row:float(row[0])):
        name=reverse.get(alias)
        if name in seen or name not in cells:continue
        seen.add(name);cell=cells[name]
        if cell['type']!='IBUF' or name not in placed['fixed_io']:continue
        if len(cell['connections'].get('O',[]))!=1:continue
        bit=cell['connections']['O'][0];sinks=[];valid=True
        for n,c in cells.items():
            for pin,bits in c['connections'].items():
                if c['port_directions'][pin]!='input' or bit not in bits:continue
                if bits!=[bit] or pin in ('CLK','CLKA','CLKB'):valid=False;continue
                try:loc=receiver_site(cells,placed,n,pin)
                except ValueError:valid=False;continue
                sinks.append((n,pin,loc['row'],loc['column']))
        if valid and len(sinks)>=min_sinks:candidates.append(dict(source=name,slack_ns=float(slack),sinks=sinks))
    if not candidates:raise ValueError('No reported critical input with supported nonclock fanout')
    return candidates[0],candidates
