"""Cross-network occupancy audit for own data, clock and carry partitions."""
import argparse,json,hashlib
from pathlib import Path
from ownpr_device import Device,RoutingGraph,ROOT
from ownpr_clock_partition import partition
from ownpr_carry_macros import validate as validate_carry
from ownpr_image_storage import sha,load_json


def audit(placement,routes,wire):
    placement=Path(placement);routes=Path(routes);wire=Path(wire);meta=json.loads((routes/'manifest.json').read_text());wm=json.loads((wire/'manifest.json').read_text());measured=load_json(wire/'wire_delays.json')
    if meta['status']!='routed' or wm['status']!='measured' or meta['vendor_pnr_executed'] or wm['vendor_pnr_executed']:raise ValueError('Joint audit requires completed own routes')
    if measured['source_routes_sha256']!=sha(routes/'routes.json') or measured['source_nets_sha256']!=sha(routes/'nets.json') or wm['expanded_routes_sha256']!=sha(wire/'expanded_routes.json'):raise ValueError('Routing/measurement provenance differs')
    if meta['netlist_sha256']!=sha(placement/'packed.json') or meta['placement_sha256']!=sha(placement/'placement.json'):raise ValueError('Placement changed after routing')
    graph=RoutingGraph(Device(),ROOT/'results/native_rrg_001');allnets=json.loads((routes/'all_nets.json').read_text());tree=json.loads((routes/'clock_tree.json').read_text());ordinary,clock=partition(graph,allnets,tree)
    if ordinary!=json.loads((routes/'nets.json').read_text()):raise ValueError('Ordinary net partition changed')
    expanded=load_json(wire/'expanded_routes.json');owners={}
    for name,net in expanded['nets'].items():
        for row in net['parents']:
            node=tuple(row['node'])
            if node in owners and owners[node]!=name:raise ValueError('Physical data-node collision')
            owners[node]=name
    for row in tree['parents']:
        node=tuple(row['node'])
        if node in owners and owners[node]!=clock['net']:raise ValueError('Dedicated clock conflicts with ordinary data route')
        owners[node]=clock['net']
    design=json.loads((placement/'packed.json').read_text());placed=json.loads((placement/'placement.json').read_text())
    carry=validate_carry(design,placed,graph.device);wide=None
    if placed.get('mux_clusters'):
        from ownpr_mux_macros import validate as validate_mux
        wide=validate_mux(design,placed,graph.device)
    ioreg=None
    if placed.get('ioreg_clusters'):
        from ownpr_ioreg_macros import validate as validate_ioreg,reserved_points
        ioreg=validate_ioreg(design,placed,graph.device)
        for point in reserved_points(design,placed):
            if point in owners:raise ValueError('Dedicated I/O output/static control is occupied by data or clock routing')
    return dict(status='data_clock_occupancy_and_carry_pattern_checked',ordinary_nets=len(ordinary),clock_sinks=clock['sinks'],physical_nodes=len(owners),
        no_data_clock_node_conflicts=True,carry_links=len(carry['links']),carry_capacity_verified=True,wide_mux_capacity=wide,ioreg_capacity=ioreg,
        complete_design_connectivity_verified=False,configuration_verified=False,timing_signoff=False,
        source_sha256=dict(routes=sha(routes/'routes.json'),clock=sha(routes/'clock_tree.json'),expanded=sha(wire/'expanded_routes.json')),
        pending=['dedicated carry electrical/timing validation','combined primitive/clock configuration','full setup/hold/recovery/removal and I/O STA'])


if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('--placement',type=Path,required=True);ap.add_argument('--routes',type=Path,required=True);ap.add_argument('--wire',type=Path,required=True);a=ap.parse_args();r=audit(a.placement,a.routes,a.wire)
    (a.routes/'joint_route_audit.json').write_text(json.dumps(r,indent=2));print(json.dumps(r,indent=2))
