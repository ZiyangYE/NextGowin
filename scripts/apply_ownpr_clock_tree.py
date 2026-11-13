"""Replace a clock tree while preserving exact own placement and data routes."""
import argparse,json,shutil
from pathlib import Path
from ownpr_device import Device,RoutingGraph,ROOT
from ownpr_clock_partition import partition
from ownpr_clock_configuration import ClockChoices
from ownpr_clock_graph import ClockGraph
from ownpr_place import sha


def apply(placement,routes,clock,out):
    placement=Path(placement);routes=Path(routes);clock=Path(clock);out=Path(out)
    meta=json.loads((routes/'manifest.json').read_text());tree=json.loads(clock.read_text());nets=json.loads((routes/'all_nets.json').read_text())
    if meta['status']!='routed' or meta['vendor_pnr_executed'] or meta['netlist_sha256']!=sha(placement/'packed.json') or meta['placement_sha256']!=sha(placement/'placement.json'):raise ValueError('Unverified frozen data routing')
    graph=RoutingGraph(Device(),ROOT/'results/native_rrg_001');model=ClockGraph(ROOT/'results/native_clock_inventory_005');ordinary,info=partition(graph,nets,tree,model)
    if ordinary!=json.loads((routes/'nets.json').read_text()):raise ValueError('Clock replacement changed ordinary endpoints')
    _,configuration=ClockChoices(graph.device,model).requirements(tree)
    if configuration['conflicts']:raise ValueError('Replacement clock choices conflict')
    occupied={n for row in json.loads((routes/'routes.json').read_text())['nets'].values() for n in row['nodes']}
    placed=json.loads((placement/'placement.json').read_text())
    from ownpr_ioreg_macros import reserved_points
    for r,c,i in reserved_points(json.loads((placement/'packed.json').read_text()),placed):occupied.add(graph.node(r-1,c-1,graph.device.names[i]))
    for row in tree['parents']:
        r,c,i=row['node']
        if r>=1 and c>=1 and i in graph.device.names and graph.node(r-1,c-1,graph.device.names[i]) in occupied:raise ValueError('Replacement clock overlaps data or a dedicated output')
    out.mkdir(exist_ok=False)
    for name in ('routes.json','nets.json','all_nets.json','static_connections.json'):shutil.copyfile(routes/name,out/name)
    shutil.copyfile(clock,out/'clock_tree.json');meta.update(status='routed',dedicated_clock=dict(info,tree_sha256=sha(out/'clock_tree.json')),
        previous_clock_tree_sha256=sha(routes/'clock_tree.json'),clock_tree_sha256=sha(out/'clock_tree.json'),
        source_data_routes_sha256=sha(routes/'routes.json'),data_routes_byte_identical=True,
        clock_replacement_configuration_pending=True,timing_signoff=False,bitstream_generated=False)
    (out/'manifest.json').write_text(json.dumps(meta,indent=2));return meta


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--placement',type=Path,required=True);p.add_argument('--routes',type=Path,required=True);p.add_argument('--clock-tree',type=Path,required=True);p.add_argument('--out',type=Path,required=True);a=p.parse_args()
    r=apply(a.placement,a.routes,a.clock_tree,a.out);print(r['status'],r['data_routes_byte_identical'],r['clock_tree_sha256'])
