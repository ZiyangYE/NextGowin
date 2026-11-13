import json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from ownpr_device import Device,RoutingGraph
from ownpr_fuse_audit import SwitchChoices
from ownpr_router import validate


def test_defaults_are_zero_selected_real_muxes_in_captured_physical_tiles():
    device=Device();choices=SwitchChoices(device);rows=device.hard_output_connections()
    assert sum(map(len,rows.values()))==5148 and len(rows)==234
    for typ,edges in rows.items():
        assert len(edges)==22
        for edge in edges:
            values=choices.template(typ)[edge['source_name'],edge['destination_name']]
            assert values and set(values.values())=={0}


def test_all_previously_failed_output_roots_match_independent_native_fanouts():
    from ownpr_image_storage import sha
    folder=ROOT/'results/vexriscv_hard_output_topology_001';meta=json.loads((folder/'manifest.json').read_text())
    assert not meta['vendor_pnr_executed'] and sha(folder/'fanouts.json')==meta['fanouts_sha256']
    graph=RoutingGraph(Device(),ROOT/'results/native_rrg_001')
    for record in json.loads((folder/'fanouts.json').read_text()):
        r,c,source=record['source'];node=graph.node(r-1,c-1,graph.device.names[source])
        actual={tuple(pip) for _,_,pip in graph.downhill(node)}
        for rr,cc,dest in record['destinations']:
            if (rr,cc)==(r,c) and dest in graph.allowed:
                assert (r-1,c-1,graph.device.names[source],graph.device.names[dest]) in actual


def test_thirty_failed_connections_have_independently_valid_paths():
    from collections import defaultdict
    graph=RoutingGraph(Device(),ROOT/'results/native_rrg_001')
    nets={n['name']:n for n in json.loads((ROOT/'results/vexriscv_hard_data_route_008/nets.json').read_text())}
    result=json.loads((ROOT/'results/vexriscv_route_diagnosis_001/hard_default_all_reachability.json').read_text())
    assert result['connections']==result['routed']==30 and not result['whole_design_routed']
    for row in result['records']:
        net=dict(nets[row['net']],sinks=[row['sink']])
        audit=validate(graph,[net],dict(nets={net['name']:dict(pips=row['pips'])}))
        assert audit['connectivity_verified'] and audit['no_node_conflicts']
