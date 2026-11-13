"""Compare recorded router work without treating partial routes as implementations."""
import argparse,collections,json,time
from pathlib import Path
from ownpr_image_storage import load_json,sha
from workspace_budget import reserve

ROOT=Path(__file__).resolve().parents[1]

def summarize(folder):
    manifest=load_json(folder/'manifest.json')
    events=[json.loads(s) for s in (folder/'progress.jsonl').read_text().splitlines() if s.strip()]
    nets={n['name']:n for n in load_json(folder/'nets.json')}
    work=collections.defaultdict(lambda:dict(completed_sink_events=0,recorded_timing_expansions=0,recorded_guide_expansions=0,completed_net_seconds=0.,failed_net_events=0))
    for event in events:
        if 'net' not in event:continue
        row=work[event['net']]
        if event['event']=='sink_done':
            row['completed_sink_events']+=1
            row['recorded_timing_expansions']+=event.get('expanded',0)
            guide=event.get('guide') or {}
            row['recorded_guide_expansions']+=guide.get('guide_expanded',0)
        if event['event']=='net_done':
            row['completed_net_seconds']+=event.get('seconds',0)
            row['failed_net_events']+=bool(event.get('failed'))
    ranked=[]
    for name,row in work.items():
        ranked.append(dict(net=name,fanout=len(nets.get(name,{}).get('sinks',[])),**row))
    ranked.sort(key=lambda r:r['completed_net_seconds'],reverse=True)
    return dict(folder=str(folder),status=manifest['status'],wall_seconds=manifest.get('total_wall_seconds',manifest.get('wall_seconds')),
        netlist_sha256=manifest['netlist_sha256'],placement_sha256=manifest['placement_sha256'],
        policy={k:manifest.get(k) for k in ('guided_corridor','search_bias','root_arrival_weight','guide_edge_delay_ns_surrogate')},
        estimated_pips=manifest.get('estimated_pips'),net_count=len(nets),
        event_counts=dict(collections.Counter(e['event'] for e in events)),
        iteration_events=[e for e in events if e['event']=='iteration_done'],
        recorded_timing_expansions=sum(r['recorded_timing_expansions'] for r in ranked),
        completed_net_seconds=sum(r['completed_net_seconds'] for r in ranked),top_completed_work=ranked[:20],
        last_events=events[-5:],progress_sha256=sha(folder/'progress.jsonl'),manifest_sha256=sha(folder/'manifest.json'))

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--runs',nargs='+',type=Path,required=True);p.add_argument('--out',type=Path,required=True);a=p.parse_args()
    reserve(ROOT,1000000)
    if a.out.exists():raise FileExistsError(a.out)
    rows=[summarize(f.resolve()) for f in a.runs]
    report=dict(status='diagnostic_only',created_epoch=time.time(),same_netlist_and_placement=len({(r['netlist_sha256'],r['placement_sha256']) for r in rows})==1,
        limitation='Counts describe emitted completed-search events only; interrupted searches and some fallback work are absent. Routing success still requires physical validation and native whole-tree STA.',runs=rows)
    a.out.write_text(json.dumps(report,indent=2));print(json.dumps({k:report[k] for k in ('status','same_netlist_and_placement')}))

if __name__=='__main__':main()
