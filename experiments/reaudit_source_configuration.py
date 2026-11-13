"""Recheck real source/target PIPs using independently derived terminal modes."""
import argparse,json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from ownpr_device import Device,RoutingGraph
from ownpr_source_configuration import stage,load
from ownpr_incremental_routes import source_validation_graph,partition
from ownpr_router import validate
from ownpr_wide_ff_packing import configure_graph
from ownpr_image_storage import load_json,sha
from workspace_budget import reserve


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for n in ('source-placement','source-routes','target-placement','target-routes','out'):p.add_argument('--'+n,type=Path,required=True)
    a=p.parse_args();reserve(ROOT,25_000_000);a.out.mkdir(exist_ok=False)
    record=stage(a.source_routes,a.target_placement,a.out,a.source_placement)
    design,layout,checked=load(a.out,a.source_routes)
    target=load_json(a.target_placement/'packed.json');placed=load_json(a.target_placement/'placement.json');tm=load_json(a.target_routes/'manifest.json')
    if tm['netlist_sha256']!=sha(a.target_placement/'packed.json') or tm['placement_sha256']!=sha(a.target_placement/'placement.json'):raise ValueError('Target configuration provenance mismatch')
    graph=RoutingGraph(Device(),ROOT/'results/native_rrg_001');configure_graph(graph,target,placed)
    old=load_json(a.source_routes/'routes.json');source=source_validation_graph(graph,old,ROOT/'results/native_rrg_001',design=design,placement=layout)
    newnets=load_json(a.target_routes/'nets.json');oldnets=load_json(a.source_routes/'nets.json')
    from ownpr_route_design import bind
    from ownpr_incremental_routes import signature
    expected,_=bind(design,layout,source)
    if (a.source_routes/'clock_tree.json').exists():
        from ownpr_clock_partition import partition as clock_partition
        from ownpr_clock_graph import ClockGraph
        tree=load_json(a.source_routes/'clock_tree.json');expected,_=clock_partition(source,expected,tree,ClockGraph.for_tree(tree))
    expected_signatures={n['name']:signature(n) for n in expected}
    if {n['name']:signature(n) for n in oldnets}!=expected_signatures:raise ValueError('Source bindings do not derive from actual old configuration')
    import copy
    forged=copy.deepcopy(oldnets);forged_routes=copy.deepcopy(old)
    first,second=forged[0]['name'],forged[1]['name'];forged[0]['name'],forged[1]['name']=second,first
    forged_routes['nets'][first],forged_routes['nets'][second]=forged_routes['nets'][second],forged_routes['nets'][first]
    physical_control=validate(source,forged,forged_routes)
    if {n['name']:signature(n) for n in forged}==expected_signatures:raise ValueError('Non-vacuous source-binding mutation failed')
    source_audit=validate(source,oldnets,old);target_audit=validate(graph,newnets,load_json(a.target_routes/'routes.json'))
    _,_,_,transition=partition(graph,oldnets,old,newnets,source_graph=source)
    files=[a.source_routes/'manifest.json',a.source_routes/'routes.json',a.source_routes/'nets.json',a.target_routes/'manifest.json',a.target_routes/'routes.json',a.target_routes/'nets.json',a.target_placement/'packed.json',a.target_placement/'placement.json',a.out/'reuse_source/manifest.json',a.out/'reuse_source/packed.json',a.out/'reuse_source/placement.json',Path(__file__),ROOT/'scripts/ownpr_source_configuration.py',ROOT/'scripts/ownpr_incremental_routes.py']
    result=dict(status='actual_source_and_target_modes_revalidated',source_context=record,source_audit=source_audit,target_audit=target_audit,transition=transition,
                actual_source_endpoint_derivation_verified=True,negative_control=dict(physically_connected_renamed_source_passes_connectivity=physical_control['connectivity_verified'],wrong_logical_binding_rejected=True),
                source_selected_terminals=sorted(source.selected_terminal_pips),target_selected_terminals=sorted(graph.selected_terminal_pips),
                native_sta_rerun=False,frequency_claim_changed=False,timing_signoff=False,source_sha256={str(f.resolve()):sha(f) for f in files})
    (a.out/'manifest.json').write_text(json.dumps(result,indent=2));print(json.dumps({k:result[k] for k in ('status','source_audit','target_audit')}))


if __name__=='__main__':main()
