"""Rank audited donor trees with identical endpoints and bounded blockers."""
import argparse,json,sys
from pathlib import Path
from ownpr_device import ROOT
from ownpr_image_storage import load_json,sha
from ownpr_incremental_routes import signature
from ownpr_route_reference import select
from workspace_budget import reserve

def main():
    p=argparse.ArgumentParser()
    for name in ('report','donor-report','out'):p.add_argument('--'+name,type=Path,required=True)
    p.add_argument('--case',default='vexriscv');p.add_argument('--max-blockers',type=int,default=2);a=p.parse_args()
    if not 0<=a.max_blockers<=5:p.error('Bounded blockers must be 0..5')
    reserve(ROOT,10_000_000);base=next(r for r in load_json(a.report)['designs'] if r['design']==a.case)
    for path,digest in base['selected_source_sha256'].items():
        if sha(ROOT/path)!=digest:raise ValueError('Base evidence changed')
    state={k:ROOT/v for k,v in base['selected'].items()};contract={k:ROOT/v for k,v in base['constraints'].items()};donor,audit=select(a.donor_report,a.case,state,contract)
    row=next(r for r in load_json(a.donor_report)['designs'] if r['design']==a.case)
    bn={r['name']:r for r in load_json(state['routes']/'nets.json')};dn={r['name']:r for r in load_json(donor/'nets.json')};br=load_json(state['routes']/'routes.json')['nets'];dr=load_json(donor/'routes.json')['nets']
    bw=load_json(state['wire']/'wire_delays.json');dw=load_json(ROOT/row['selected']['wire']/'wire_delays.json')
    if bw['source_routes_sha256']!=sha(state['routes']/'routes.json') or dw['source_routes_sha256']!=sha(donor/'routes.json'):raise ValueError('Wire measurement differs from route source')
    sys.path.insert(0,str(ROOT/'experiments'));from analyze_ownpr_critical_wires import analyze
    ranking=analyze(state['sta'],state['placement'],state['routes'],64);critical=ranking['ranked_nets'];owners={}
    route_meta=load_json(state['routes']/'manifest.json')
    if 'search_weights_sha256' in route_meta:
        weights=state['routes']/'search_weights.json'
        if sha(weights)!=route_meta['search_weights_sha256']:raise ValueError('Receiver binding changed')
        receivers=load_json(weights)['policy']['receivers']
    else:
        from ownpr_pin_cut_selection import rebuild_receivers
        receivers,_=rebuild_receivers(state['routes'],state['placement'],route_meta)
    endpoints={(r['net'],name+'/'+pin):r['node'] for r in receivers for name,pin in r['receivers']}
    for name,tree in br.items():
        for node in tree['nodes']:
            if node in owners and owners[node]!=name:raise ValueError('Base contains conflicting route ownership')
            owners[node]=name
    options=[]
    for ranked in critical:
        name=ranked['net'];net=bn[name]
        if name not in dn or net.get('clock') or 'constant_value' in net or signature(net)!=signature(dn[name]):continue
        nodes=set(dr[name]['nodes']);blockers=sorted({owners[v] for v in nodes if v in owners and owners[v]!=name})
        if len(blockers)>a.max_blockers or any(bn[b].get('clock') or 'constant_value' in bn[b] or len(bn[b]['sinks'])>64 or nodes&({bn[b]['source']}|set(bn[b]['sinks'])) for b in blockers):continue
        old={s['resource']:s for s in bw['nets'][name]['sinks']};new={s['resource']:s for s in dw['nets'][name]['sinks']}
        if set(old)!=set(new):continue
        gains={str(s):min(old[s]['delays_by_selector'][1][i]-new[s]['delays_by_selector'][1][i] for i in (2,3)) for s in old}
        labels={e['endpoint'] for path in ranking['traces'] if path['slack_ns']<=ranked['worst_path_slack_ns']+.001 for e in path['ordinary_wires'] if e['net']==name}
        if not labels or any((name,label) not in endpoints for label in labels):continue
        critical_nodes={endpoints[name,label] for label in labels}
        critical_gain=min(gains[str(s)] for s in critical_nodes)
        if critical_gain<=.05:continue
        options.append(dict(net=name,blockers=blockers,worst_path_slack_ns=ranked['worst_path_slack_ns'],reported_critical_sinks=sorted(critical_nodes),critical_minimum_gain_ns=critical_gain,minimum_transition_gain_by_sink_ns=gains,maximum_sink_gain_ns=max(gains.values()),base_pips=len(br[name]['pips']),donor_pips=len(dr[name]['pips'])))
    options.sort(key=lambda r:(r['worst_path_slack_ns'],-r['critical_minimum_gain_ns'],len(r['blockers'])))
    result=dict(status='donor_candidates_not_accepted',donor_reference=audit,base_report_sha256=sha(a.report),max_blockers=a.max_blockers,scope='Donor measurements rank trials; blocker reroute, all other sinks, hold and full STA still required',candidates=options)
    with a.out.open('x') as stream:json.dump(result,stream,indent=2)
    print(json.dumps(options[:5],indent=2))
if __name__=='__main__':main()
