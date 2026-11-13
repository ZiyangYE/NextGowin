"""Explain complete reported setup-path deficits; never predict routed signoff.

The counterfactuals hold cells, clocks and IO budgets fixed. They remove some
wire delay mathematically, without asserting that such routes exist. Hold,
unreported alternate paths and changed loading must still be reanalyzed.
"""
import argparse,json,re
from collections import Counter
from pathlib import Path
from ownpr_device import ROOT
from ownpr_image_storage import load_json,read_text,sha
from routing_path_diagnosis import diagnose
from project_timing import section


def barriers(paths,period,targets):
    if not paths or period<=0:raise ValueError('Missing paths/period')
    records=[]
    for target in targets:
        if not 0<target<=1000:raise ValueError('Invalid target')
        change=period-1000/target
        failing=[p for p in paths if p['slack_ns']<change]
        groups=Counter(p['source'] for p in failing)
        rows=[]
        for source,count in groups.most_common():
            own=[p for p in failing if p['source']==source]
            other=[period-p['slack_ns'] for p in paths if p['source']!=source]
            rows.append(dict(source=source,reported_paths=count,worst_deficit_ns=max(change-p['slack_ns'] for p in own),
                unaffected_path_ceiling_mhz=1000/max(other) if other and max(other)>0 else None))
        records.append(dict(target_mhz=target,reported_paths_needing_relief=len(failing),launch_groups=len(groups),
            worst_required_relief_ns=max([0.]+[change-p['slack_ns'] for p in failing]),
            top_five_launch_groups_coverage=sum(v for _,v in groups.most_common(5)),groups=rows))
    ideal=[]
    for fraction in (1.,.75,.5,.25,0.):
        demands=[(period-p['slack_ns']-(1-fraction)*p['data_wire_ns'],p['path']) for p in paths]
        required,path=max(demands)
        ideal.append(dict(retained_wire_fraction=fraction,period_ns=required,reported_path_frequency_ceiling_mhz=1000/required if required>0 else None,limiting_reported_path=path))
    return dict(targets=records,uniform_wire_counterfactuals=ideal)


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--sta',type=Path,required=True);ap.add_argument('--placement',type=Path,required=True)
    ap.add_argument('--targets',type=float,nargs='+',default=[80,90,100,150,200]);ap.add_argument('--out',type=Path,required=True);a=ap.parse_args()
    from workspace_budget import reserve
    reserve(ROOT,20_000_000)
    native=load_json(a.sta/'manifest.json')
    if native['packed_sha256']!=sha(a.placement/'packed.json') or native['placement_sha256']!=sha(a.placement/'placement.json'):raise ValueError('STA/netlist mismatch')
    report=read_text(a.sta/'probe.tr');sdc=read_text(a.sta/'probe.sdc')
    clocks=re.findall(r'^\s*create_clock\s+-name\s+clk\s+-period\s+([\d.]+)',sdc,re.M)
    if len(clocks)!=1:raise ValueError('Only the checked single primary clock is supported')
    period=float(clocks[0]);paths=diagnose(report)
    # Do not extrapolate half-cycle/asynchronous relationships as full periods.
    details=section(report,'3.3.1 Setup Analysis Report')
    relations=re.findall(r'^Setup Relationship:\s*([-\d.]+)',details,re.M)
    table=section(report,'3.1.1 Setup Paths Table')
    endpoints=re.findall(r'^\s*\d+\s+[-\d.]+\s+\S+\s+\S+\s+',table,re.M)
    if len(paths)!=len(endpoints) or len(paths)!=len(relations) or any(abs(float(v)-period)>.0011 for v in relations):raise ValueError('Incomplete or non-full-period setup coverage')
    result=barriers(paths,period,a.targets)
    mapping=load_json(a.sta/'name_map.json')['instances'];reverse={v:k for k,v in mapping.items()};module=load_json(a.placement/'packed.json')['modules']['top']
    sources={}
    for alias in {p['source'] for p in paths}:
        name=reverse.get(alias);cell=module['cells'].get(name,{})
        outputs={b for pin,bs in cell.get('connections',{}).items() if cell['port_directions'][pin]=='output' for b in bs}
        sources[alias]=dict(instance=name,primitive=cell.get('type'),net_labels=[n for n,v in module.get('netnames',{}).items() if outputs.intersection(v['bits'])])
    for row in result['targets']:
        for group in row['groups']:group.update(sources[group['source']])
    limiting={r['limiting_reported_path'] for r in result['uniform_wire_counterfactuals']}
    result['counterfactual_limiting_paths']=[dict(p,source_identity=sources[p['source']],destination_instance=reverse.get(p['destination'])) for p in paths if p['path'] in limiting]
    result.update(period_ns=period,reported_setup_paths=len(paths),all_reported_setup_paths_accounted=True,
        report_sha256=sha(a.sta/'probe.tr'),packed_sha256=sha(a.placement/'packed.json'),
        top_paths=[dict(p,source_identity=sources[p['source']]) for p in paths[:20]],
        scope='Reported full-period setup witnesses only. Counterfactual ceilings are not achievable-frequency predictions, do not cover alternate paths, and do not check hold. Fixed cell/clock/IO contributions assumed.',
        vendor_pnr_executed=False,netlist_modified=False)
    a.out.parent.mkdir(parents=True,exist_ok=True);a.out.write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(dict(paths=len(paths),targets=[{k:v for k,v in r.items() if k!='groups'} for r in result['targets']],counterfactuals=result['uniform_wire_counterfactuals']),indent=2))


if __name__=='__main__':main()
