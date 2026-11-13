"""Rank shared data-wire bottlenecks in reported setup paths, not full graph STA."""
import argparse,json,re
from collections import defaultdict
from pathlib import Path
from extract_timing import ROW
from ownpr_image_storage import read_text,sha
from ownpr_device import ROOT
from workspace_budget import reserve


def analyze(text,aliases):
    sections=re.split(r'(?m)^3\.(?:3|7)\.\d+ (Setup|Hold|Recovery|Removal) Analysis Report[^\n]*\n',text)
    paths=[];edges={}
    def logical(node):
        instance,sep,pin=node.rpartition('/')
        if not sep:return aliases.get(node,node)
        if instance not in aliases:raise ValueError('Unknown path instance: '+instance)
        return aliases[instance]+'/'+pin
    for kind,body in zip(sections[1::2],sections[2::2]):
        if kind!='Setup':continue
        for block in body.split('Path Summary:')[1:]:
            def field(label):
                m=re.search(r'(?m)^'+label+r'\s*:\s*([^\r\n]+)',block)
                if not m:raise ValueError('Missing path field '+label)
                return m[1].strip()
            slack=float(field('Slack'))
            if slack>=0:continue
            start,end=field('From'),field('To')
            arrival=block.split('Data Arrival Path:',1)[1].split('Data Required Path:',1)[0]
            rows=[]
            for line in arrival.splitlines():
                m=ROW.match(line)
                if m:
                    at,delay,typ,rf,fanout,loc,node=m.groups()
                    rows.append(dict(node=node,delay=float(delay),kind=typ,fanout=int(fanout)))
            # Begin at launch Q, or at the input-pad data arc. Clock insertion
            # and clock-net delays must not become data-wire candidates.
            indices=[i for i,r in enumerate(rows) if r['kind']=='tC2Q']
            if indices:first=indices[0]
            else:
                indices=[i for i,r in enumerate(rows) if r['node'].rpartition('/')[0]==start and r['kind'] in ('tINS','tIn')]
                if not indices:raise ValueError('Unsupported launch path: '+start)
                first=indices[0]
            data=rows[first:];key=(start,end,tuple(r['node'] for r in data))
            if any(p['_key']==key for p in paths):continue
            row=dict(_key=key,source=aliases.get(start,start),endpoint=logical(data[-1]['node']),slack_ns=slack,
                     data_wire_ns=sum(r['delay'] for r in data if r['kind']=='tNET'),
                     cell_ns=sum(r['delay'] for r in data if r['kind'] in ('tINS','tC2Q')),edges=[])
            for prev,r in zip(data,data[1:]):
                if r['kind']!='tNET':continue
                pair=(logical(prev['node']),logical(r['node']));row['edges'].append(pair)
                if pair not in edges:edges[pair]=dict(source=pair[0],sink=pair[1],endpoints=set(),paths=0,weighted_deficit_ns=0.,maximum_wire_ns=0.,maximum_fanout=0)
                e=edges[pair];e['paths']+=1;e['endpoints'].add(row['endpoint']);e['weighted_deficit_ns']+=-slack;e['maximum_wire_ns']=max(e['maximum_wire_ns'],r['delay']);e['maximum_fanout']=max(e['maximum_fanout'],prev['fanout'])
            paths.append(row)
    if not paths:raise ValueError('No violating setup paths found')
    for p in paths:p.pop('_key')
    ranked=[]
    for e in edges.values():
        e['endpoint_count']=len(e['endpoints']);e['endpoints']=sorted(e['endpoints']);ranked.append(e)
    ranked.sort(key=lambda e:(-e['weighted_deficit_ns'],-e['maximum_wire_ns'],e['source'],e['sink']))
    return dict(status='reported_critical_data_wire_overlap',reported_violating_paths=len(paths),unique_reported_endpoints=len({p['endpoint'] for p in paths}),
                full_timing_graph_coverage=False,ranking_is_gain_bound=False,paths=sorted(paths,key=lambda p:p['slack_ns']),ranked_edges=ranked)


def main():
    p=argparse.ArgumentParser();p.add_argument('--sta',type=Path,required=True);p.add_argument('--out',type=Path,required=True);a=p.parse_args()
    reserve(ROOT,10_000_000)
    aliases={v:k for k,v in json.loads((a.sta/'name_map.json').read_text())['instances'].items()}
    report=analyze(read_text(a.sta/'probe.tr'),aliases)
    report.update(report_sha256=sha(a.sta/'probe.tr'),name_map_sha256=sha(a.sta/'name_map.json'))
    with a.out.open('x',encoding='utf-8') as f:json.dump(report,f,indent=2)
    print(json.dumps({k:v for k,v in report.items() if k not in ('paths','ranked_edges')}))
    print(json.dumps([{k:v for k,v in e.items() if k!='endpoints'} for e in report['ranked_edges'][:8]],indent=2))


if __name__=='__main__':main()
