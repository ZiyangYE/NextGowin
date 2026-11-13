"""Audit structural candidates against one immutable, IO-qualified baseline."""
import argparse,json,re
from collections import Counter
from pathlib import Path
from ownpr_device import ROOT
from ownpr_image_storage import load_json,read_text,sha
from summarize_path_regions import endpoint_table
from routing_path_diagnosis import diagnose
from project_timing import section
from extract_timing import ROW


def named_paths(folder):
    reverse={v:k for k,v in load_json(folder/'name_map.json')['instances'].items()}
    def name(pin):
        alias,sep,suffix=pin.partition('/')
        return reverse.get(alias,alias)+sep+suffix
    paths=diagnose(read_text(folder/'probe.tr'))
    for p in paths:
        p['source']=name(p['source']);p['destination']=name(p['destination'])
        for e in p['edges']:
            e['source']=name(e['source']);e['destination']=name(e['destination'])
    arcs={}
    body=section(read_text(folder/'probe.tr'),'3.3.1 Setup Analysis Report')
    for chunk in re.split(r'^\s*Path\d+\s*$',body,flags=re.M)[1:]:
        arrival=chunk.split('Data Arrival Path:',1)[1].split('Data Required Path:',1)[0]
        rows=[m.groups() for line in arrival.splitlines() if (m:=ROW.match(line))]
        start=next((i for i,r in enumerate(rows) if r[2] in ('tC2Q','tIn')),None)
        if start is None:continue
        for i in range(start+1,len(rows)):
            r,prev=rows[i],rows[i-1]
            if r[2]!='tNET':continue
            key=(name(prev[6]),name(r[6]),r[3])
            arcs.setdefault(key,set()).add(float(r[1]))
    return paths,arcs


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--report',type=Path,required=True);p.add_argument('--runs',type=Path,nargs='+',required=True);p.add_argument('--out',type=Path,required=True);a=p.parse_args()
    from workspace_budget import reserve
    reserve(ROOT,20_000_000)
    baseline=load_json(a.report)['designs'][0]
    for name,digest in baseline['selected_source_sha256'].items():
        if sha(ROOT/name)!=digest:raise ValueError('Baseline changed')
    state={k:ROOT/v for k,v in baseline['selected'].items()}
    old=load_json(state['placement']/'packed.json')['modules']['top'];oldp=load_json(state['placement']/'placement.json')
    oldsta,period=endpoint_table(state['sta']);oldpaths,oldarcs=named_paths(state['sta']);counts=Counter(c['type'] for c in old['cells'].values());records=[]
    for folder in a.runs:
        run=load_json(folder/'manifest.json');candidate=folder/'candidate';native=folder/'implementation/export/native'
        nm=load_json(native/'manifest.json');em=load_json(folder/'implementation/export/manifest.json');cm=load_json(candidate/'manifest.json')
        if run['status'] not in ('accepted','rejected') or run['wall_seconds']>run['budget_seconds'] or run['budget_seconds']>3600:raise ValueError('Incomplete or over-budget attempt')
        if em['status']!='verified_handoff_complete' or nm['packed_sha256']!=sha(candidate/'packed.json') or nm['placement_sha256']!=sha(candidate/'placement.json'):raise ValueError('Incomplete physical export')
        if nm['cst_sha256']!=sha(ROOT/baseline['constraints']['cst']) or nm['vendor_placement_executed'] or nm['vendor_routing_executed']:raise ValueError('Physical contract differs')
        cells=load_json(candidate/'packed.json')['modules']['top'];placed=load_json(candidate/'placement.json')
        if cells['ports']!=old['ports'] or placed['fixed_io']!=oldp['fixed_io'] or placed['hard_clusters']!=oldp['hard_clusters']:raise ValueError('Fixed boundary changed')
        if cm.get('logical_netlist_byte_identical'):
            if sha(candidate/'packed.json')!=sha(state['placement']/'packed.json'):raise ValueError('Claimed unchanged netlist differs')
            proof_kind='Independently verified byte-identical logical netlist'
        else:
            proof=cm['proof'];proof_kind='CEC or inductive state proof'
            if isinstance(proof,dict):
                if proof['status']!='register_replica_invariant_proved':raise ValueError('State proof failed')
                checks=proof['transition_step']+proof['observable_equivalence']
            else:checks=proof
            if not checks or not all(r['cec']['equivalent'] for r in checks):raise ValueError('CEC failed')
        observed,newperiod=endpoint_table(native);shift=period-newperiod
        delta=[dict(endpoint=n,delta_ns=observed[n]-(oldsta[n]-shift)) for n in sorted(set(oldsta)&set(observed))]
        paths,arcs=named_paths(native)
        matched=[dict(source=k[0],destination=k[1],transition=k[2],baseline_delay_ns=next(iter(oldarcs[k])),candidate_delay_ns=next(iter(v)),delta_ns=next(iter(v))-next(iter(oldarcs[k]))) for k,v in arcs.items() if k in oldarcs and len(v)==len(oldarcs[k])==1]
        current=Counter(c['type'] for c in cells['cells'].values())
        moved=[n for n,r in oldp['clusters'].items() if n in placed['clusters'] and placed['clusters'][n]!=r]
        route=load_json(folder/'implementation/route/manifest.json');inc=route.get('incremental',{});branches=inc.get('branches',{})
        route_summary=dict(route_seconds=route.get('total_wall_seconds'),reused_nets=inc.get('reused_nets'),rerouted_nets=inc.get('rerouted_nets'),
            fallback_full_routing=inc.get('fallback_full_routing'),branch_reuse={k:v for k,v in branches.items() if k!='records'})
        records.append(dict(run=folder.as_posix(),status=run['status'],metrics=run['metrics'],wall_seconds=run['wall_seconds'],qualified_mhz=run.get('qualified_mhz'),qualified_metrics=run.get('qualified_metrics'),
            resource_growth={t:current[t]-counts[t] for t in sorted(current|counts) if current[t]!=counts[t]},moved_original_clusters=len(moved),moved_original_registers=sum(bool(oldp['clusters'][n].get('ff')) for n in moved),
            matching_setup_endpoints=len(delta),improved_over_10ps=sum(r['delta_ns']>.01 for r in delta),regressed_over_10ps=sum(r['delta_ns']<-.01 for r in delta),largest_endpoint_regressions=sorted(delta,key=lambda r:r['delta_ns'])[:10],
            top_paths=paths[:5],matched_wire_regressions=sorted(matched,key=lambda r:-r['delta_ns'])[:10],matched_wire_improvements=sorted(matched,key=lambda r:r['delta_ns'])[:10],
            unchanged_original_cell_placements=all(n in placed['clusters'] and placed['clusters'][n]==r for n,r in oldp['clusters'].items()),
            routing=route_summary,logical_equivalence_evidence=proof_kind,
            manifest_sha256=sha(folder/'manifest.json'),netlist_sha256=sha(candidate/'packed.json'),placement_sha256=sha(candidate/'placement.json'),report_sha256=sha(native/'probe.tr')))
    controls=[]
    for i,left in enumerate(records):
        for right in records[i+1:]:
            if left['netlist_sha256']!=right['netlist_sha256']:continue
            control=dict(left=left['run'],right=right['run'],netlist_byte_identical=True,placement_byte_identical=left['placement_sha256']==right['placement_sha256'])
            if control['placement_byte_identical']:
                lf=ROOT/left['run']/'implementation/route';rf=ROOT/right['run']/'implementation/route'
                control['clock_tree_byte_identical']=sha(lf/'clock_tree.json')==sha(rf/'clock_tree.json')
                control['physical_nets_byte_identical']=sha(lf/'nets.json')==sha(rf/'nets.json')
                la=load_json(lf/'routes.json')['nets'];ra=load_json(rf/'routes.json')['nets']
                control['changed_route_trees']=sum(set(map(tuple,la[n]['pips']))!=set(map(tuple,ra[n]['pips'])) for n in la)
            controls.append(control)
    qualified=[(1000/period,a.report)]
    for r in records:
        if r['status']!='accepted':continue
        checkpoint=ROOT/r['run']/'qualified_replay_checkpoint.json';selected=load_json(checkpoint)['designs'][0]
        if not selected['metrics']['native_constraints_satisfied'] or not selected['metrics']['other_checks_passed']:raise ValueError('Selected frequency is not qualified')
        for name,digest in selected['selected_source_sha256'].items():
            if sha(ROOT/name)!=digest:raise ValueError('Selected result changed')
        qualified.append((r['qualified_mhz'],checkpoint))
    frequency,checkpoint=max(qualified,key=lambda r:r[0])
    result=dict(baseline=baseline['metrics'],baseline_top_paths=oldpaths[:5],runs=records,controls=controls,selected_qualified_mhz=frequency,
        selected_checkpoint=checkpoint.resolve().relative_to(ROOT).as_posix(),selected_checkpoint_sha256=sha(checkpoint),goal_complete=False,vendor_pnr_executed=False,
        scope='Complete IO incremental experiments. Endpoint slacks normalized by period; launches may differ. Matched wire arcs require same source/destination pins and transition, but changed loads are allowed. Not cold RTL runtime or generic superiority.')
    a.out.parent.mkdir(parents=True,exist_ok=True);a.out.write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps([{k:r[k] for k in ('run','status','metrics','wall_seconds','resource_growth','moved_original_clusters','moved_original_registers')} for r in records],indent=2))


if __name__=='__main__':main()
