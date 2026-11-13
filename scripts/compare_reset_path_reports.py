"""Compare reported reset endpoints by logical identity, never by native alias.

This is a diagnostic over reported setup paths, not a substitute for full STA.
Missing endpoints are reported explicitly; different branches are not summed.
"""
import argparse,json,re
from pathlib import Path
from workspace_budget import reserve
from ownpr_device import ROOT
from ownpr_image_storage import read_text,sha

def paths(folder,source):
    aliases={v:k for k,v in json.loads((folder/'name_map.json').read_text())['instances'].items()}
    text=read_text(folder/'probe.tr');result={}
    # Restrict to setup reports: later hold/recovery sections must not overwrite.
    sections=re.split(r'(?m)^3\.(?:3|7)\.\d+(?:\.\d+)? (Setup|Hold|Recovery|Removal) Analysis Report[^\n]*\n',text)
    for kind,body in zip(sections[1::2],sections[2::2]):
        if kind!='Setup':continue
        for block in body.split('Path Summary:')[1:]:
            def field(label):
                match=re.search(r'(?m)^'+re.escape(label)+r'\s*:\s*([^\r\n]+)',block)
                if not match:raise ValueError('Missing path field: '+label)
                return match.group(1).strip()
            launch=field('From').split('/')[0];capture=field('To').split('/')[0]
            if aliases.get(launch)!=source:continue
            if capture not in aliases:raise ValueError('Unknown endpoint alias')
            arrival=block.split('Data Arrival Path:',1)[1].split('Data Required Path:',1)[0]
            arcs=[]
            for line in arrival.splitlines():
                match=re.match(r'\s*([-\d.]+)\s+([-\d.]+)\s+(t\w+)\s+.*?\s+(\S+)\s*$',line)
                if match:arcs.append(dict(arrival_ns=float(match[1]),delay_ns=float(match[2]),kind=match[3],node=match[4]))
            row=dict(endpoint=aliases[capture],arrival_ns=float(field('Data Arrival Time')),slack_ns=float(field('Slack')),arcs=arcs)
            key=aliases[capture]
            if key not in result or row['slack_ns']<result[key]['slack_ns']:result[key]=row
    if not result:raise ValueError('No reported reset setup paths')
    return result

def main():
    p=argparse.ArgumentParser()
    for name in ('baseline','candidate','out'):p.add_argument('--'+name,type=Path,required=True)
    p.add_argument('--source',default='ng_pad_135');a=p.parse_args();reserve(ROOT,5_000_000)
    before=paths(a.baseline,a.source);after=paths(a.candidate,a.source);common=before.keys()&after.keys()
    rows=[dict(endpoint=n,baseline=before[n],candidate=after[n],arrival_gain_ns=before[n]['arrival_ns']-after[n]['arrival_ns'],slack_gain_ns=after[n]['slack_ns']-before[n]['slack_ns']) for n in common]
    report=dict(status='reported_endpoint_diagnostic',full_sta_acceptance=False,baseline_report_sha256=sha(a.baseline/'probe.tr'),candidate_report_sha256=sha(a.candidate/'probe.tr'),baseline_reported_endpoints=len(before),candidate_reported_endpoints=len(after),matched_endpoints=len(common),only_baseline=sorted(before.keys()-after.keys()),only_candidate=sorted(after.keys()-before.keys()),paths=sorted(rows,key=lambda r:r['arrival_gain_ns']))
    with a.out.open('x') as stream:json.dump(report,stream,indent=2)
    print(json.dumps({k:v for k,v in report.items() if k!='paths'}))
if __name__=='__main__':main()
