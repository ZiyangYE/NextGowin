"""Audit a completed all-pin cut experiment against its checked baseline."""
import argparse,json,re
from collections import Counter
from pathlib import Path
from ownpr_device import ROOT
from ownpr_image_storage import load_json,read_text,sha
from summarize_path_regions import endpoint_table
from extract_timing import ROW
from workspace_budget import reserve
from ownpr_pin_cut_selection import measured_inputs


def paths(folder,count=3):
    reverse={v:k for k,v in load_json(folder/'name_map.json')['instances'].items()}
    section=re.search(r'^3\.3\.1 Setup Analysis Report\n(.*?)(?=^3\.|\Z)',read_text(folder/'probe.tr'),re.M|re.S)
    if not section:raise ValueError('Missing setup traces')
    result=[]
    for block in re.split(r'^\s*Path\d+\s*$',section[1],flags=re.M)[1:count+1]:
        arrival=re.search(r'Data Arrival Path:\n(.*?)(?=\nData Required Path:)',block,re.S);active=False;arcs=[]
        if not arrival:raise ValueError('Missing data trace')
        for line in arrival[1].splitlines():
            m=ROW.match(line)
            if not m:continue
            _,delay,kind,transition,fanout,site,label=m.groups()
            if kind in ('tC2Q','tIn'):active=True
            if active:
                alias,_,pin=label.partition('/');arcs.append(dict(kind=kind,delay_ns=float(delay),transition=transition,fanout=int(fanout),site=site,endpoint=reverse.get(alias,alias)+'/'+pin))
        result.append(dict(slack_ns=float(re.search(r'^Slack\s*:\s*([-\d.]+)',block,re.M)[1]),
            wire_ns=sum(a['delay_ns'] for a in arcs if a['kind']=='tNET'),
            cell_and_c2q_ns=sum(a['delay_ns'] for a in arcs if a['kind']!='tNET'),arcs=arcs))
    return result


def main():
    p=argparse.ArgumentParser();p.add_argument('--baseline',type=Path,required=True);p.add_argument('--run',type=Path,required=True);p.add_argument('--out',type=Path,required=True);a=p.parse_args()
    old=load_json(a.baseline)['designs'][0];run=load_json(a.run/'manifest.json');qualified=load_json(a.run/'qualified_replay_checkpoint.json')['designs'][0]
    if run['status']!='accepted' or not 0<run['wall_seconds']<=run['budget_seconds']<=3600:raise ValueError('Attempt not accepted within its original deadline')
    checks=0
    for row in (old,qualified):
        for name,h in row['selected_source_sha256'].items():
            if sha(ROOT/name)!=h:raise ValueError('Checkpoint evidence changed: '+name)
            checks+=1
    oldp=ROOT/old['selected']['placement'];newp=ROOT/qualified['selected']['placement'];oldsta=ROOT/old['selected']['sta'];native=a.run/'implementation/export/native'
    od=load_json(oldp/'packed.json')['modules']['top'];nd=load_json(newp/'packed.json')['modules']['top'];op=load_json(oldp/'placement.json');np=load_json(newp/'placement.json');cm=load_json(newp/'manifest.json')
    for key in ('fixed_io','hard_clusters'):
        if op[key]!=np[key]:raise ValueError('Fixed I/O or hard sites changed')
    if od['ports']!=nd['ports'] or cm['source_netlist_sha256']!=sha(oldp/'packed.json') or cm['netlist_sha256']!=sha(newp/'packed.json'):raise ValueError('CEC source chain mismatch')
    if not cm['proof'] or not all(r['cec']['equivalent'] for r in cm['proof']):raise ValueError('CEC missing')
    if old['constraints']['cst']!=qualified['constraints']['cst']:raise ValueError('CST path changed')
    em=load_json(a.run/'implementation/export/manifest.json');qm=load_json(a.run/'qualification/export/manifest.json')
    for m in (em,qm):
        if m['status']!='verified_handoff_complete' or m['vendor_placement_executed'] or m['vendor_routing_executed']:raise ValueError('Incomplete own P&R handoff')
    if not qualified['metrics']['native_constraints_satisfied'] or any(qualified['metrics'][k] for k in ('setup_violations','hold_violations')):raise ValueError('Frequency not qualified')
    old_end,oldperiod=endpoint_table(oldsta);new_end,newperiod=endpoint_table(native)
    if set(old_end)!=set(new_end):raise ValueError('Unexpected endpoint set change in combinational mapping')
    rows=[dict(endpoint=n,old_slack_at_target=old_end[n]-(oldperiod-newperiod),new_slack=new_end[n],delta_ns=new_end[n]-(old_end[n]-(oldperiod-newperiod))) for n in sorted(old_end)]
    oc=Counter(c['type'] for c in od['cells'].values());nc=Counter(c['type'] for c in nd['cells'].values())
    positions=lambda p:{r['ff']:[r[k] for k in ('row','column','slot')] for r in p['clusters'].values() if r.get('ff')}
    of,nf=positions(op),positions(np)
    if set(of)!=set(nf):raise ValueError('Combinational mapping changed state instances')
    oldw,oldwm=measured_inputs(ROOT/old['selected']['routes'],ROOT/old['selected']['wire'],oldp)
    neww,newwm=measured_inputs(ROOT/qualified['selected']['routes'],ROOT/qualified['selected']['wire'],newp)
    control_wires=[]
    for name in sorted(of):
        if od['cells'][name]!=nd['cells'][name]:raise ValueError('State primitive changed')
        for pin in ('CE','RESET','SET','CLEAR','PRESET'):
            if (name,pin) in oldw and (name,pin) in neww:
                before,after=oldw[name,pin],neww[name,pin]
                control_wires.append(dict(ff=name,pin=pin,old_site=of[name],new_site=nf[name],old_wire_ns=before,new_wire_ns=after,delta_ns=after-before))
    discovery=cm['pin_discovery'];plan=load_json(newp/'plan.json')
    selected=cm['coverage']['selected_roots'];extra=set(discovery['additional_roots_not_in_reported_paths'])
    result=dict(status='accepted_pin_frontier_audited',baseline_report_sha256=sha(a.baseline),qualified_report_sha256=sha(a.run/'qualified_replay_checkpoint.json'),checkpoint_hashes_verified=checks,
        wall_seconds=run['wall_seconds'],budget_seconds=run['budget_seconds'],qualified_mhz=run['qualified_mhz'],qualified_metrics=qualified['metrics'],target_metrics=run['metrics'],target_achieved=run['target_achieved'],
        baseline_metrics=old['metrics'],resource_delta={k:nc[k]-oc[k] for k in sorted(oc|nc) if nc[k]!=oc[k]},original_ff_positions_changed=sum(of[n]!=nf[n] for n in of),state_instances_unchanged=True,fixed_io_and_hard_sites_unchanged=True,
        eligible_roots=discovery['eligible_roots'],considered_roots=discovery['considered_roots'],roots_absent_from_path_listing=len(extra),selected_roots=selected,selected_roots_absent_from_path_listing=[n for n in selected if n in extra],
        endpoints=dict(count=len(rows),old_period_ns=oldperiod,target_period_ns=newperiod,improved_over_10ps=sum(r['delta_ns']>.01 for r in rows),regressed_over_10ps=sum(r['delta_ns']<-.01 for r in rows),largest_improvements=sorted(rows,key=lambda r:-r['delta_ns'])[:15],largest_regressions=sorted(rows,key=lambda r:r['delta_ns'])[:15]),
        control_wire_regressions=sorted(control_wires,key=lambda r:-r['delta_ns'])[:10],wire_sources=dict(before=oldwm,after=newwm),
        worst_paths=paths(native),vendor_pnr_executed=False,goal_complete=False,
        scope='Bounded incremental experiment with complete I/O STA; worst launch paths may change. No cold RTL run or generic vendor superiority established.')
    reserve(ROOT,1_000_000)
    if a.out.exists():raise FileExistsError(a.out)
    a.out.parent.mkdir(parents=True,exist_ok=True);a.out.write_text(json.dumps(result,indent=2))
    print(json.dumps({k:v for k,v in result.items() if k not in ('endpoints','worst_paths')},indent=2))


if __name__=='__main__':main()
