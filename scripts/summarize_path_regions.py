"""Audit bounded regional attempts and compare original STA endpoint slacks."""
import argparse,json,re
from collections import Counter
from pathlib import Path
from ownpr_device import ROOT
from ownpr_image_storage import load_json,read_text,sha
from project_timing import section


def endpoint_table(folder):
    reverse={v:k for k,v in load_json(folder/'name_map.json')['instances'].items()}
    body=section(read_text(folder/'probe.tr'),'3.1.1 Setup Paths Table');rows={}
    for _,slack,source,target in re.findall(r'^\s*(\d+)\s+([-\d.]+)\s+(\S+)\s+(\S+)\s+',body,re.M):
        alias,sep,pin=target.partition('/');key=reverse.get(alias,alias)+sep+pin
        if key in rows:raise ValueError('Duplicate setup endpoint')
        rows[key]=float(slack)
    period=float(re.search(r'create_clock\s+-name\s+clk\s+-period\s+([\d.]+)',read_text(folder/'probe.sdc'))[1])
    if not rows:raise ValueError('No endpoint table')
    return rows,period


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--report',type=Path,required=True);p.add_argument('--runs',type=Path,nargs='+',required=True);p.add_argument('--out',type=Path,required=True);a=p.parse_args()
    from workspace_budget import reserve
    reserve(ROOT,20_000_000);a.out.mkdir(parents=True,exist_ok=False)
    baseline=load_json(a.report)['designs'][0]
    for name,digest in baseline['selected_source_sha256'].items():
        if sha(ROOT/name)!=digest:raise ValueError('Baseline changed')
    state={k:ROOT/v for k,v in baseline['selected'].items()};old=load_json(state['placement']/'packed.json')['modules']['top'];oldp=load_json(state['placement']/'placement.json');counts=Counter(c['type'] for c in old['cells'].values())
    oldsta,period=endpoint_table(state['sta']);records=[];hashes={}
    for folder in a.runs:
        run=load_json(folder/'manifest.json');candidate=folder/'candidate';native=folder/'implementation/export/native';nm=load_json(native/'manifest.json');em=load_json(folder/'implementation/export/manifest.json')
        if run['status'] not in ('accepted','rejected') or run['wall_seconds']>run['budget_seconds'] or run['budget_seconds']>3600:raise ValueError('Incomplete or over-budget attempt')
        if em['status']!='verified_handoff_complete' or nm['packed_sha256']!=sha(candidate/'packed.json') or nm['placement_sha256']!=sha(candidate/'placement.json'):raise ValueError('Incomplete physical export')
        if nm['cst_sha256']!=sha(ROOT/baseline['constraints']['cst']) or nm['vendor_placement_executed'] or nm['vendor_routing_executed']:raise ValueError('Physical contract differs')
        cells=load_json(candidate/'packed.json')['modules']['top'];placed=load_json(candidate/'placement.json');proof=load_json(candidate/'proof.json');plan=load_json(candidate/'plan.json')
        if cells['ports']!=old['ports'] or placed['fixed_io']!=oldp['fixed_io'] or placed['hard_clusters']!=oldp['hard_clusters']:raise ValueError('Fixed boundary changed')
        if proof['status']!='register_replica_invariant_proved' or not all(r['cec']['equivalent'] for r in proof['transition_step']+proof['observable_equivalence']):raise ValueError('Missing state/observation proof')
        observed,newperiod=endpoint_table(native);shift=period-newperiod
        differences=[dict(endpoint=n,old_slack_at_candidate_period=oldsta[n]-shift,new_slack=observed[n],delta_ns=observed[n]-(oldsta[n]-shift)) for n in sorted(set(oldsta)&set(observed))]
        extra=[dict(endpoint=n,slack_ns=observed[n]) for n in sorted(set(observed)-set(oldsta))]
        comparison=dict(baseline_period_ns=period,candidate_period_ns=newperiod,matching_endpoints=len(differences),new_endpoints=len(extra),
            improved_over_10ps=sum(r['delta_ns']>.01 for r in differences),regressed_over_10ps=sum(r['delta_ns']<-.01 for r in differences),
            largest_regressions=sorted(differences,key=lambda r:r['delta_ns'])[:20],largest_improvements=sorted(differences,key=lambda r:-r['delta_ns'])[:20],
            new_endpoint_slacks=sorted(extra,key=lambda r:r['slack_ns']),baseline_report_sha256=sha(state['sta']/'probe.tr'),candidate_report_sha256=sha(native/'probe.tr'),
            scope='Same original endpoint names/pins, period normalized. Worst launch paths may differ; not a matched whole-path comparison.')
        (a.out/(folder.name+'_endpoints.json')).write_text(json.dumps(comparison,indent=2))
        current=Counter(c['type'] for c in cells['cells'].values());growth={t:current[t]-counts[t] for t in sorted(current|counts) if current[t]!=counts[t]}
        moved=[n for n,r in oldp['clusters'].items() if placed['clusters'][n]!=r]
        records.append(dict(run=folder.as_posix(),status=run['status'],metrics=run['metrics'],wall_seconds=run['wall_seconds'],budget_seconds=run['budget_seconds'],
            qualified_mhz=run.get('qualified_mhz'),qualified_metrics=run.get('qualified_metrics'),resource_growth=growth,new_registers=len(plan['register_replicas']),
            moved_original_clusters=len(moved),moved_original_registers=sum(bool(oldp['clusters'][n].get('ff')) for n in moved),
            reported_paths_targeted=len({i for c in plan['selected'] for i in c['covered_paths']}),original_endpoints_improved=comparison['improved_over_10ps'],original_endpoints_regressed=comparison['regressed_over_10ps'],
            netlist_sha256=sha(candidate/'packed.json'),placement_sha256=sha(candidate/'placement.json'),proof_sha256=sha(candidate/'proof.json')))
        hashes[folder.as_posix()]=sha(folder/'manifest.json')
    result=dict(baseline=baseline['metrics'],runs=records,source_manifest_sha256=hashes,goal_complete=False,vendor_pnr_executed=False,
        scope='Actual complete incremental experiments; does not establish cold RTL timing, generic superiority or untested target frequencies.')
    (a.out/'summary.json').write_text(json.dumps(result,indent=2));print(json.dumps(records,indent=2))


if __name__=='__main__':main()
