"""Rank ordinary data nets from native worst paths; no optimization is applied."""
import argparse,json,re,sys
from pathlib import Path
from collections import defaultdict
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from ownpr_image_storage import load_json,sha
from ownpr_image_storage import read_text as read_artifact_text


def analyze(capture,placement,routes,limit):
    meta=load_json(capture/'manifest.json');design=load_json(placement/'packed.json');cells=design['modules']['top']['cells']
    if meta['routes_sha256']!=sha(routes/'routes.json') or meta['packed_sha256']!=sha(placement/'packed.json') or not meta['sta_report_generated'] or meta['vendor_routing_executed']:raise ValueError('Mismatched own implementation/report')
    reverse={v:k for k,v in load_json(capture/'name_map.json')['instances'].items()};nets={n['name']:n for n in load_json(routes/'nets.json')}
    drivers={bit:dict(instance=n,primitive=c['type'],pin=p) for n,c in cells.items() for p,bs in c['connections'].items() if c['port_directions'][p]=='output' for bit in bs}
    text=read_artifact_text(capture/'probe.tr');section=re.search(r'^3\.3\.1 Setup Analysis Report\n(.*?)(?=^3\.|\Z)',text,re.M|re.S)
    if not section:raise ValueError('Missing setup paths')
    rows={};traces=[]
    for block in re.split(r'^\s*Path\d+\s*$',section[1],flags=re.M)[1:limit+1]:
        arrival=re.search(r'Data Arrival Path:\n(.*?)(?=\nData Required Path:)',block,re.S)
        slack=re.search(r'^Slack\s*:\s*([-\d.]+)',block,re.M);active=False;trace=[]
        if not arrival or not slack:raise ValueError('Incomplete critical path trace')
        for line in arrival[1].splitlines():
            m=re.match(r'^\s*[-\d.]+\s+([-\d.]+)\s+(\w+)\s+(RR|RF|FR|FF)\s+\d+\s+(\S+)\s+(own_i_\d+)/(\S+)\s*$',line)
            if not m:continue
            delay,kind,transition,site,alias,pin=m.groups()
            if kind in ('tC2Q','tIn'):active=True
            if not active or kind!='tNET':continue
            name=reverse[alias];c=cells[name];parsed=re.fullmatch(r'(\w+)(?:\[(\d+)\])?',pin)
            if parsed is None:raise ValueError('Unknown pin spelling')
            port,index=parsed[1],int(parsed[2] or 0);bit=c['connections'][port][index];net='net_'+str(bit)
            if net not in nets:continue  # Dedicated carry and direct paired FF links are audited separately.
            trace.append(dict(net=net,endpoint=name+'/'+pin,site=site,wire_delay_ns=float(delay)))
            row=rows.setdefault(net,dict(net=net,driver=drivers[bit],fanout=len(nets[net]['sinks']),path_occurrences=0,maximum_reported_wire_ns=0.,total_reported_wire_ns=0.,worst_path_slack_ns=float(slack[1]),endpoints=[]))
            row['path_occurrences']+=1;row['maximum_reported_wire_ns']=max(row['maximum_reported_wire_ns'],float(delay));row['total_reported_wire_ns']+=float(delay)
            row['worst_path_slack_ns']=min(row['worst_path_slack_ns'],float(slack[1]))
            if name+'/'+pin not in row['endpoints']:row['endpoints'].append(name+'/'+pin)
        traces.append(dict(slack_ns=float(slack[1]),ordinary_wires=trace))
    ranked=sorted(rows.values(),key=lambda r:(r['worst_path_slack_ns'],-r['maximum_reported_wire_ns'],-r['total_reported_wire_ns'],r['net']))
    return dict(status='diagnostic_ranking_not_an_accepted_optimization',paths=len(traces),ranked_nets=ranked,traces=traces,
        sta_report_verified=meta.get('sta_completed',False),full_sta_qualified=load_json(capture/'handoff_audit.json')['native_sta_constraints_satisfied'] if meta.get('sta_completed') else False,
        source_sha256={str(p):sha(p) for p in (capture/'probe.tr',placement/'packed.json',routes/'routes.json')})


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--capture',type=Path,required=True);p.add_argument('--placement',type=Path,required=True);p.add_argument('--routes',type=Path,required=True);p.add_argument('--paths',type=int,default=32);p.add_argument('--out',type=Path,required=True);a=p.parse_args()
    if not 1<=a.paths<=1000:p.error('Path limit must be 1..1000')
    result=analyze(a.capture,a.placement,a.routes,a.paths);a.out.write_text(json.dumps(result,indent=2));print(json.dumps(result['ranked_nets'][:3],indent=2))
