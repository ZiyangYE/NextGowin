import sys,json
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from ownpr_device import Device,RoutingGraph,ROOT
from ownpr_image_storage import load_json
from ownpr_route_design import bind


def test_logical_sink_inventory_matches_native_endpoints_without_changing_nets():
    base=ROOT/'results/vexriscv_path_regions_004';placement=base/'candidate';route=base/'implementation/route'
    graph=RoutingGraph(Device(),ROOT/'results/native_rrg_001');design=load_json(placement/'packed.json');places=load_json(placement/'placement.json');receivers=[]
    nets,_=bind(design,places,graph,hard_bindings=load_json(route/'hard_endpoint_audit.json'),logical_sinks=receivers)
    assert json.loads(json.dumps(nets))==load_json(route/'all_nets.json')
    bynet={n['name']:n for n in nets}
    native={(r['instance'],r['pin']):r for r in load_json(base/'qualification/export/native/sta_endpoint_audit.json') if r['direction']=='In'}
    checked=0
    for row in receivers:
        expected=native[row['instance'],row['pin']]
        nodes={graph.node(r-1,c-1,graph.device.names[i]) for entry in expected['nodes'] for r,c,i in [entry['node']] if min(r,c)>=1 and i in graph.device.names}
        if not nodes and expected['direct_connection']:
            # Native paired LUT->FF D has no ordinary wire node of its own.
            assert row['pin']=='D' and design['modules']['top']['cells'][row['instance']]['type'].startswith('DFF')
            assert row['node']==bynet[row['net']]['source']
            continue
        assert row['node'] in nodes,(row,expected)
        checked+=1
    assert checked>14000
