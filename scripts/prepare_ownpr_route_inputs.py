"""Bind own placed primitives before clock planning; does not route a net."""
import argparse,json
from pathlib import Path
from ownpr_device import Device,RoutingGraph,ROOT
from ownpr_route_design import bind
from ownpr_place import sha


def main():
    p=argparse.ArgumentParser();p.add_argument('--placement',type=Path,required=True);p.add_argument('--out',type=Path,required=True);a=p.parse_args();a.out.mkdir(exist_ok=False)
    design=json.loads((a.placement/'packed.json').read_text());placed=json.loads((a.placement/'placement.json').read_text());meta=json.loads((a.placement/'manifest.json').read_text())
    if meta['status']!='placed' or meta['placement_sha256']!=sha(a.placement/'placement.json') or meta['netlist_sha256']!=sha(a.placement/'packed.json'):raise ValueError('Unverified placement for route binding')
    nets,static=bind(design,placed,RoutingGraph(Device(),ROOT/'results/native_rrg_001'))
    (a.out/'all_nets.json').write_text(json.dumps(nets,indent=2));(a.out/'static_connections.json').write_text(json.dumps(static,indent=2))
    r=dict(status='bound_not_routed',placement_sha256=meta['placement_sha256'],netlist_sha256=meta['netlist_sha256'],net_count=len(nets),
        vendor_pnr_executed=False,timing_signoff=False,scope='Endpoint binding for clock planning only. No route result exists.')
    (a.out/'manifest.json').write_text(json.dumps(r,indent=2));print(json.dumps(r))


if __name__=='__main__':main()
