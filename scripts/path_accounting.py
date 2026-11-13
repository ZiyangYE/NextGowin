"""Account for cell and routing delays in the native reported setup paths."""
import argparse
import hashlib
import json
from pathlib import Path
import re
from extract_timing import ROW
from project_timing import section


def account(text):
    body=section(text,'3.3.1 Setup Analysis Report')
    if not body:raise ValueError('No main setup analysis section')
    chunks=re.split(r'^\s*Path(\d+)\s*$',body,flags=re.M);paths=[]
    for index in range(1,len(chunks),2):
        number,chunk=int(chunks[index]),chunks[index+1]
        summary={k.strip():v.strip() for k,v in re.findall(r'^([A-Za-z ]+?)[ \t]*:[ \t]*(.*?)[ \t]*$',chunk.split('Data Arrival Path:')[0],re.M)}
        if 'Data Arrival Path:' not in chunk or 'Data Required Path:' not in chunk:continue
        arrival=chunk.split('Data Arrival Path:',1)[1].split('Data Required Path:',1)[0]
        rows=[m.groups() for line in arrival.splitlines() if (m:=ROW.match(line))]
        start=next((i for i,r in enumerate(rows) if r[2] in ('tC2Q','tIn')),None)
        if start is None:continue
        values={}
        for row in rows[start:]:values[row[2]]=values.get(row[2],0)+float(row[1])
        total=sum(values.values())
        if total<=0:continue
        paths.append(dict(path=number,slack_ns=float(summary['Slack']),source=summary.get('From'),
            destination=summary.get('To'),launch_clock=summary.get('Launch Clk'),capture_clock=summary.get('Latch Clk'),
            data_delay_sum_ns=total,route_delay_ns=values.get('tNET',0),
            cell_delay_ns=total-values.get('tNET',0),delay_by_type_ns=values,
            route_fraction=values.get('tNET',0)/total,
            scope='Sum of rounded data-path increments starting at launch C2Q/input; launch clock tree and capture constraints excluded'))
    if not paths:raise ValueError('No supported reported setup data path')
    return paths


def main():
    ap=argparse.ArgumentParser();ap.add_argument('report',type=Path);ap.add_argument('--out',type=Path,required=True)
    args=ap.parse_args();result=dict(report_sha256=hashlib.sha256(args.report.read_bytes()).hexdigest(),
        scope='Reported main setup paths only; not every device path or a complete wire-delay model',
        paths=account(args.report.read_text(errors='replace')))
    args.out.parent.mkdir(parents=True,exist_ok=True);args.out.write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result['paths'][0],indent=2))


if __name__=='__main__':main()
