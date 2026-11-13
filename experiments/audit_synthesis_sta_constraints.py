"""Check a native synthesis diagnostic before trusting it as a timing oracle."""
import argparse
import html
import json
from pathlib import Path
import re


def tables(text):
    clean=lambda s:' '.join(html.unescape(re.sub(r'<[^>]*>',' ',s)).split())
    return [[[clean(v) for v in re.findall(r'<t[dh]\b[^>]*>(.*?)</t[dh]>',row,re.S|re.I)]
             for row in re.findall(r'<tr\b[^>]*>(.*?)</tr>',table,re.S|re.I)]
            for table in re.findall(r'<table\b[^>]*>(.*?)</table>',text,re.S|re.I)]


ap=argparse.ArgumentParser();ap.add_argument('folder',type=Path);ap.add_argument('--sdc',type=Path,required=True)
ap.add_argument('--profile',type=Path,required=True);args=ap.parse_args();p=args.folder
text=(p/'timing.rpt.html').read_text();ts=tables(text);sdc=args.sdc.read_text()
expected=json.loads(args.profile.read_text())['clock_periods_ns'];names={};targets={}
for line in sdc.splitlines():
    if line.startswith('create_clock '):
        name=re.search(r'-name\s+(\S+)',line);port=re.search(r'get_ports\s+\{([^}]+)\}',line)
        if name and port:names[port[1]]=name[1]
    elif line.startswith('create_generated_clock '):
        name=re.search(r'-name\s+(\S+)',line);pins=re.findall(r'get_pins\s+\{([^}]+)\}',line)
        if name and pins:targets[pins[-1]]=name[1]
clock_table=next((t for t in ts if t and t[0][:4]==['NO.','Clock Name','Type','Period']),None)
if clock_table is None:raise ValueError('Native diagnostic has no clock table')
observed={};aliases={};rows=[]
for row in clock_table[1:]:
    if len(row)!=10:raise ValueError('Unexpected native clock row shape')
    alias=names.get(row[1],targets.get(row[9],row[1]));aliases[row[1]]=alias
    observed[alias]=float(row[3]);rows.append(dict(native_name=row[1],matched_name=alias,period_ns=float(row[3]),object=row[9]))
mismatch={name:dict(expected_ns=period,observed_ns=observed.get(name)) for name,period in expected.items()
          if name not in observed or abs(observed[name]-period)>.000501}
unexpected=set(observed)-set(expected)
async_groups=[]
for line in sdc.splitlines():
    if line.startswith('set_clock_groups -asynchronous'):
        async_groups.append([set(g.split()) for g in re.findall(r'-group\s+\[get_clocks\s+\{([^}]+)\}\]',line)])
paths=[]
for table in ts:
    data={row[0]:row[1] for row in table if len(row)==2}
    if 'Launch Clk' not in data or 'Latch Clk' not in data:continue
    strip=lambda s:re.sub(r'\[[RF]\]$','',s)
    launch=aliases.get(strip(data['Launch Clk']),strip(data['Launch Clk']))
    capture=aliases.get(strip(data['Latch Clk']),strip(data['Latch Clk']))
    for groups in async_groups:
        a=next((i for i,g in enumerate(groups) if launch in g),None)
        b=next((i for i,g in enumerate(groups) if capture in g),None)
        if a is not None and b is not None and a!=b:
            paths.append(dict(launch=launch,capture=capture,from_node=data.get('From'),to_node=data.get('To')));break
result=dict(scope='Synthesis-report diagnostic only; not physical signoff.',clock_rows=rows,
            clock_mismatches=mismatch,unexpected_clocks=sorted(unexpected),reported_async_paths=paths,
            usable_as_original_constraint_oracle=not mismatch and not unexpected and not paths)
(p/'constraint_audit.json').write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps({k:v for k,v in result.items() if k not in ('clock_rows','reported_async_paths')},indent=2))
print('Reported paths crossing declared asynchronous clock groups:',len(paths))
