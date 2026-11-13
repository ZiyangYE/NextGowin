"""Admit only independently checked, conflict-free prefixes from failed routes."""
from collections import defaultdict
from ownpr_router import validate


def safe_seeds(graph,nets,checkpoint,blocked_nodes=()):
    if checkpoint.get('status')!='incomplete_route_checkpoint':raise ValueError('Expected an explicitly incomplete route checkpoint')
    byname={n['name']:n for n in nets};endpoints=defaultdict(set);occupied=set(blocked_nodes);accepted={};rejected=[]
    if set(checkpoint['nets'])-set(byname):raise ValueError('Checkpoint contains unknown nets')
    for net in nets:
        for node in set(net.get('sources',[net['source']]))|set(net['sinks']):endpoints[node].add(net['name'])
    order=sorted(checkpoint['nets'],key=lambda n:(-len(checkpoint['nets'][n].get('retained_sinks',[])),n))
    for name in order:
        record=checkpoint['nets'][name];net=byname[name];retained=record.get('retained_sinks',[])
        if not retained or len(retained)!=len(set(retained)) or not set(retained)<=set(net['sinks']):raise ValueError('Invalid checkpoint sink subset')
        roots=set(net.get('sources',[net['source']]));actual=set(roots);parents={}
        for r,c,a,b in record['pips']:
            before,after=graph.node(r,c,a),graph.node(r,c,b)
            if after in roots or after in parents:raise ValueError('Checkpoint has a root cycle or multiple parents')
            parents[after]=before;actual.update((before,after))
        if actual!=set(record['nodes']):raise ValueError('Checkpoint node occupancy differs from PIPs')
        validate(graph,[dict(net,sinks=retained)],dict(nets={name:record}))
        if actual&occupied:
            rejected.append(dict(net=name,reason='shared_or_reserved_resource'));continue
        if any(endpoints[node]-{name} for node in actual):
            rejected.append(dict(net=name,reason='other_net_endpoint_occupied'));continue
        accepted[name]=record;occupied.update(actual)
    retained_nets=[dict(byname[name],sinks=record['retained_sinks']) for name,record in accepted.items()]
    audit=validate(graph,retained_nets,dict(nets=accepted))
    return accepted,dict(status='safe_checkpoint_prefixes_verified',retained_nets=len(accepted),
        retained_sinks=sum(len(r['retained_sinks']) for r in accepted.values()),rejected=rejected,audit=audit,
        complete_design_connectivity_verified=False,timing_signoff=False)


def negotiation_trees(graph,nets,checkpoint,blocked_nodes=()):
    """Validate each partial tree; shared resources remain negotiable conflicts."""
    if checkpoint.get('status')!='incomplete_route_checkpoint':raise ValueError('Expected incomplete negotiation state')
    byname={n['name']:n for n in nets}
    if set(checkpoint['nets'])-set(byname):raise ValueError('Unknown checkpoint net')
    accepted={};rejected=[];usage=defaultdict(set)
    for name,record in checkpoint['nets'].items():
        rows,check=safe_seeds(graph,[byname[name]],dict(status='incomplete_route_checkpoint',nets={name:record}),blocked_nodes)
        accepted.update(rows);rejected.extend(check['rejected'])
        if name in rows:
            for node in record['nodes']:usage[node].add(name)
    return accepted,dict(status='individual_checkpoint_trees_verified',retained_nets=len(accepted),
        retained_sinks=sum(len(r['retained_sinks']) for r in accepted.values()),overused_nodes=sum(len(v)>1 for v in usage.values()),
        rejected=rejected,branches_frozen=False,complete_design_connectivity_verified=False,timing_signoff=False)


def from_unrouted(nets,result):
    if result.get('status')!='unrouted':raise ValueError('Only an explicitly unrouted result may become an incomplete checkpoint')
    byname={n['name']:n for n in nets}
    if set(result['nets'])-set(byname):raise ValueError('Unknown result net')
    records={}
    for name,record in result['nets'].items():
        retained=sorted(set(record['nodes'])&set(byname[name]['sinks']))
        if retained:records[name]=dict(record,retained_sinks=retained)
    checkpoint=dict(status='incomplete_route_checkpoint',nets=records,iterations=result['iterations'],
        connectivity_verified=False,no_node_conflicts=False,timing_signoff=False)
    if 'history' in result:checkpoint['history']=result['history']
    return checkpoint


if __name__=='__main__':
    import argparse,json,shutil,time
    from pathlib import Path
    from ownpr_device import Device,RoutingGraph,ROOT
    from ownpr_image_storage import sha,load_json
    from workspace_budget import reserve
    p=argparse.ArgumentParser();p.add_argument('--routes',type=Path,required=True);p.add_argument('--out',type=Path,required=True);p.add_argument('--placement',type=Path);a=p.parse_args()
    start=time.monotonic();source=a.routes.resolve();out=a.out.resolve();meta=json.loads((source/'manifest.json').read_text())
    if meta['status']!='failed' or meta['vendor_pnr_executed'] is not False:raise ValueError('Expected failed own-routing attempt')
    nets=json.loads((source/'nets.json').read_text());result=json.loads((source/'routes.json').read_text());cp=from_unrouted(nets,result)
    graph=RoutingGraph(Device(),ROOT/'results/native_rrg_001')
    if a.placement:
        if sha(a.placement/'packed.json')!=meta['netlist_sha256'] or sha(a.placement/'placement.json')!=meta['placement_sha256']:raise ValueError('Checkpoint placement does not match source')
        from ownpr_wide_ff_packing import configure_graph
        configure_graph(graph,load_json(a.placement/'packed.json'),load_json(a.placement/'placement.json'))
    _,audit=negotiation_trees(graph,nets,cp)
    reserve(ROOT,30_000_000);out.mkdir(exist_ok=False)
    for name in ('nets.json','all_nets.json','clock_tree.json'):shutil.copyfile(source/name,out/name)
    (out/'partial_routes.json').write_text(json.dumps(cp))
    meta.update(derived_checkpoint_from=str(source),original_manifest_sha256=sha(source/'manifest.json'),original_routes_sha256=sha(source/'routes.json'),
        partial_routes_sha256=sha(out/'partial_routes.json'),checkpoint_nets=len(cp['nets']),checkpoint_admission_audit=audit,
        checkpoint_derivation_source_sha256=sha(Path(__file__)),checkpoint_derivation_seconds=time.monotonic()-start)
    meta['cumulative_route_seconds']+=meta['checkpoint_derivation_seconds']
    (out/'manifest.json').write_text(json.dumps(meta,indent=2));print(json.dumps(audit,indent=2))
