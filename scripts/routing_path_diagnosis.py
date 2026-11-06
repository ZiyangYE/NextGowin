"""Measured path edges, separated from launch clock insertion and cell delay."""
import argparse
import json
from pathlib import Path
import re
from extract_timing import ROW
from project_timing import section
from run_abc_reproduction import sha


def coordinates(loc):
    m=re.match(r'R(\d+)C(\d+)',loc)
    return [int(m[1]),int(m[2])] if m else None


def diagnose(text):
    body=section(text,'3.3.1 Setup Analysis Report');chunks=re.split(r'^\s*Path(\d+)\s*$',body,flags=re.M);paths=[]
    for i in range(1,len(chunks),2):
        number,chunk=int(chunks[i]),chunks[i+1]
        if 'Data Arrival Path:' not in chunk or 'Data Required Path:' not in chunk:continue
        summary={k.strip():v.strip() for k,v in re.findall(r'^([A-Za-z ]+?)[ \t]*:[ \t]*(.*?)[ \t]*$',chunk.split('Data Arrival Path:')[0],re.M)}
        arrival=chunk.split('Data Arrival Path:',1)[1].split('Data Required Path:',1)[0]
        rows=[m.groups() for line in arrival.splitlines() if (m:=ROW.match(line))]
        start=next((j for j,r in enumerate(rows) if r[2] in ('tC2Q','tIn')),None)
        if start is None:continue
        edges=[]
        for j in range(start+1,len(rows)):
            r=rows[j];prev=rows[j-1]
            if r[2]!='tNET':continue
            a,b=coordinates(prev[5]),coordinates(r[5])
            edges.append(dict(source=prev[6],destination=r[6],delay_ns=float(r[1]),source_fanout=int(prev[4]),
                source_location=prev[5],destination_location=r[5],manhattan_tiles=sum(abs(x-y) for x,y in zip(a,b)) if a and b else None))
        data=rows[start:];wire=sum(float(r[1]) for r in data if r[2]=='tNET');cell=sum(float(r[1]) for r in data if r[2]!='tNET')
        paths.append(dict(path=number,slack_ns=float(summary['Slack']),source=summary['From'],destination=summary['To'],
            launch_clock_delay_ns=sum(float(r[1]) for r in rows[:start]),data_wire_ns=wire,data_cell_ns=cell,
            wire_edges=len(edges),edges_over_1ns=sum(e['delay_ns']>1 for e in edges),
            zero_delay_edges=sum(e['delay_ns']==0 for e in edges),
            nonzero_wire_edges=sum(e['delay_ns']>0 for e in edges),
            fabric_endpoint_span_sum=sum(e['manhattan_tiles'] or 0 for e in edges),
            selector_wire_ns=sum(e['delay_ns'] for e in edges if re.search(r'/S[01]$',e['destination'])),
            top_three_wire_fraction=sum(sorted((e['delay_ns'] for e in edges),reverse=True)[:3])/wire if wire else 0,
            edges=sorted(edges,key=lambda e:-e['delay_ns'])))
    if not paths:raise ValueError('No supported setup paths')
    return paths


if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('report',type=Path);ap.add_argument('--out',type=Path,required=True);a=ap.parse_args()
    r=dict(report_sha256=sha(a.report),scope='Reported paths only; endpoint Manhattan span is not routed wire length.',paths=diagnose(a.report.read_text()))
    a.out.parent.mkdir(parents=True,exist_ok=True);a.out.write_text(json.dumps(r,indent=2)+'\n')
    print(json.dumps(r['paths'][0],indent=2))
