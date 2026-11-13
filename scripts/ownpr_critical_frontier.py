"""Rank shared measured wire boundaries across audited setup endpoint paths.

This is candidate prioritization, not a prediction of achievable timing gain.
Only one reported worst path per endpoint is represented; hidden paths require
through-pin feedback and final complete STA before accepting any change.
"""
import argparse,json
from pathlib import Path
from collections import defaultdict
from ownpr_image_storage import load_json,read_text,sha
from routing_path_diagnosis import diagnose
from run_ownpr_pin_closure import metric
from workspace_budget import reserve


def analyze(sta,window=.3):
    observed=metric(sta);manifest=load_json(sta/'manifest.json')
    audit=load_json(sta/'handoff_audit.json')
    if manifest['handoff_audit_sha256']!=sha(sta/'handoff_audit.json') or audit['source_sha256']['probe.tr']!=sha(sta/'probe.tr'):
        raise ValueError('Changed STA evidence')
    mapping=load_json(sta/'name_map.json');reverse={v:k for k,v in mapping['instances'].items()}
    paths=diagnose(read_text(sta/'probe.tr'))
    if len(paths)!=audit['timing']['path_coverage']['setup']['paths']:
        raise ValueError('Incomplete parsed setup paths')
    worst=min(p['slack_ns'] for p in paths);selected=[p for p in paths if p['slack_ns']<=worst+window]
    edges={};launches=defaultdict(list)
    def logical(label):
        instance,sep,pin=label.rpartition('/')
        if not sep or instance not in reverse:raise ValueError('Unmapped timing node')
        return reverse[instance]+'/'+pin
    for p in selected:
        launches[reverse[p['source']]].append(p)
        for edge in p['edges']:
            key=logical(edge['source']),logical(edge['destination'])
            row=edges.setdefault(key,dict(source=key[0],destination=key[1],paths=set(),worst_slack_ns=p['slack_ns'],maximum_wire_ns=0.,reported_fanout=0,source_location=edge['source_location'],destination_location=edge['destination_location']))
            row['paths'].add(p['path']);row['worst_slack_ns']=min(row['worst_slack_ns'],p['slack_ns'])
            row['maximum_wire_ns']=max(row['maximum_wire_ns'],edge['delay_ns']);row['reported_fanout']=max(row['reported_fanout'],edge['source_fanout'])
    ranked=[]
    for r in edges.values():
        r['reported_path_coverage']=len(r['paths']);r['paths']=sorted(r['paths'])
        # Repeated wire cost is exposure on reported paths, not independent
        # physical delay nor a promised recoverable benefit.
        r['wire_exposure_ns']=r['maximum_wire_ns']*r['reported_path_coverage'];ranked.append(r)
    ranked.sort(key=lambda r:(-r['wire_exposure_ns'],r['worst_slack_ns'],r['source'],r['destination']))
    launch_rows=[dict(source=n,reported_path_coverage=len(ps),worst_slack_ns=min(p['slack_ns'] for p in ps),maximum_data_wire_ns=max(p['data_wire_ns'] for p in ps)) for n,ps in launches.items()]
    launch_rows.sort(key=lambda r:(-r['reported_path_coverage'],r['worst_slack_ns'],r['source']))
    return dict(scope=__doc__,metrics=observed,window_ns=window,parsed_setup_paths=len(paths),selected_paths=len(selected),worst_slack_ns=worst,
                source_sha256={str(sta/n):sha(sta/n) for n in ('manifest.json','handoff_audit.json','name_map.json','probe.tr')},
                shared_wire_boundaries=ranked,launch_groups=launch_rows)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--sta',type=Path,required=True);p.add_argument('--out',type=Path,required=True);p.add_argument('--window',type=float,default=.3);a=p.parse_args()
    if not 0<=a.window<=10:p.error('Window outside bounded diagnostic range')
    reserve(Path(__file__).resolve().parents[1],5_000_000);result=analyze(a.sta,a.window)
    with a.out.open('x') as f:json.dump(result,f,indent=2)
    print(json.dumps(dict(selected_paths=result['selected_paths'],launch_groups=result['launch_groups'][:5],boundaries=result['shared_wire_boundaries'][:5]),indent=2))
