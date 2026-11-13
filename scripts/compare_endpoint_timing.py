"""Compare the worst reported setup path per logical endpoint across mappings.

Source/path changes are explicit: deltas compare endpoint envelopes, not
causal delay changes on an identical path. Uses audited full-IO reports.
"""
import argparse,json,re,statistics
from pathlib import Path
from extract_timing import ROW
from ownpr_image_storage import load_json,read_text,sha
from run_ownpr_pin_closure import metric
from workspace_budget import reserve


def paths(text,aliases):
    section=re.search(r'^3\.3\.1 Setup Analysis Report\n(.*?)(?=^3\.|\Z)',text,re.M|re.S)
    if not section:raise ValueError('Missing setup details')
    result={}
    for block in section[1].split('Path Summary:')[1:]:
        source=re.search(r'^From\s*:\s*(\S+)',block,re.M)[1]
        slack=float(re.search(r'^Slack\s*:\s*([-\d.]+)',block,re.M)[1])
        arrival=block.split('Data Arrival Path:',1)[1].split('Data Required Path:',1)[0]
        rows=[m.groups() for line in arrival.splitlines() if (m:=ROW.match(line))]
        starts=[i for i,r in enumerate(rows) if r[2]=='tC2Q']
        if not starts:starts=[i for i,r in enumerate(rows) if r[6].rpartition('/')[0]==source and r[2] in ('tINS','tIn')]
        if not starts:raise ValueError('Unsupported data launch '+source)
        data=rows[starts[0]:]
        def logical(label):
            name,sep,pin=label.rpartition('/')
            if not sep or name not in aliases:raise ValueError('Unknown logical path node '+label)
            return aliases[name]+'/'+pin
        endpoint=logical(data[-1][6]);nodes=[logical(r[6]) for r in data]
        row=dict(endpoint=endpoint,source=aliases[source],slack_ns=slack,
            cell_ns=sum(float(r[1]) for r in data if r[2] in ('tINS','tC2Q')),
            wire_ns=sum(float(r[1]) for r in data if r[2]=='tNET'),nodes=nodes)
        if endpoint not in result or slack<result[endpoint]['slack_ns']:result[endpoint]=row
    return result


def compare(before,after):
    missing=set(before)-set(after)
    if missing:raise ValueError('Setup endpoint disappeared: '+str(sorted(missing)[:4]))
    rows=[]
    for endpoint,a in before.items():
        b=after[endpoint]
        rows.append(dict(endpoint=endpoint,before_slack_ns=a['slack_ns'],after_slack_ns=b['slack_ns'],
            slack_gain_ns=b['slack_ns']-a['slack_ns'],cell_delta_ns=b['cell_ns']-a['cell_ns'],wire_delta_ns=b['wire_ns']-a['wire_ns'],
            source_changed=a['source']!=b['source'],path_changed=a['nodes']!=b['nodes']))
    return sorted(rows,key=lambda r:r['slack_gain_ns'])


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for n in ('before','after','out'):p.add_argument('--'+n,type=Path,required=True)
    a=p.parse_args();reserve(Path(__file__).resolve().parents[1],10_000_000)
    sets=[];metrics=[]
    for folder in (a.before,a.after):
        metrics.append(metric(folder));aliases={v:k for k,v in load_json(folder/'name_map.json')['instances'].items()}
        observed=paths(read_text(folder/'probe.tr'),aliases)
        if len(observed)!=load_json(folder/'handoff_audit.json')['timing']['path_coverage']['setup']['paths']:
            raise ValueError('Parsed endpoint count differs from audited setup coverage')
        sets.append(observed)
    if load_json(a.before/'manifest.json')['source_sdc_sha256']!=load_json(a.after/'manifest.json')['source_sdc_sha256']:raise ValueError('Timing constraints differ')
    if load_json(a.before/'manifest.json')['cst_sha256']!=load_json(a.after/'manifest.json')['cst_sha256']:raise ValueError('Pin constraints differ')
    rows=compare(*sets)
    groups={}
    for label,subset in [('all',rows),('previous_violations',[r for r in rows if r['before_slack_ns']<0]),('new_violations',[r for r in rows if r['before_slack_ns']>=0 and r['after_slack_ns']<0])]:
        groups[label]=dict(endpoints=len(subset),source_changes=sum(r['source_changed'] for r in subset),
            mean_slack_gain_ns=statistics.mean(r['slack_gain_ns'] for r in subset) if subset else None,
            worst_slack_gain_ns=min((r['slack_gain_ns'] for r in subset),default=None))
    result=dict(status='compared_endpoint_worst_path_envelopes',scope=__doc__,before_metrics=metrics[0],after_metrics=metrics[1],groups=groups,endpoints=rows,
        source_reports_sha256={str(f):sha(f/'probe.tr') for f in (a.before,a.after)})
    with a.out.open('x') as f:json.dump(result,f,indent=2)
    print(json.dumps(groups,indent=2))


if __name__=='__main__':main()
