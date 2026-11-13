"""Normalize independent LUT padding to an existing native GND instance net.

Keep all physical trees exactly identical, rebind them, and redo whole-state
CEC. This repairs serialization identity, not timing constraints or routes.
"""
import argparse,copy,json,shutil
from pathlib import Path
from ownpr_device import ROOT,Device,RoutingGraph
from ownpr_image_storage import load_json,sha
from ownpr_route_design import bind
from ownpr_lut_lifting import independent
from native_boundary_cec import prove
from workspace_budget import reserve


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for n in ('source','reference','out'):p.add_argument('--'+n,type=Path,required=True)
    a=p.parse_args();reserve(ROOT,40_000_000);meta=load_json(a.source/'manifest.json');design=load_json(a.source/'placement/packed.json');layout=load_json(a.source/'placement/placement.json')
    grounds=[c['connections']['G'][0] for c in design['modules']['top']['cells'].values() if c['type']=='GND']
    if not grounds or meta['status']!='routed':raise ValueError('No existing GND or completed source routing')
    changed=[]
    for n,c in design['modules']['top']['cells'].items():
        if c['type'] not in ('LUT1','LUT2','LUT3','LUT4'):continue
        for pin,bs in c['connections'].items():
            if pin.startswith('I') and bs==['0']:
                if not independent(c,int(pin[1:])):raise ValueError('Literal input is functionally dependent')
                c['connections'][pin]=[grounds[0]];changed.append([n,pin])
    if not changed:raise ValueError('No independent padding literals to normalize')
    graph=RoutingGraph(Device(),ROOT/'results/native_rrg_001');logical=[];allnets,static=bind(design,layout,graph,hard_bindings=load_json(a.source/'hard_endpoint_audit.json'),logical_sinks=logical)
    if json.loads(json.dumps(allnets))!=load_json(a.source/'all_nets.json') or json.loads(json.dumps(static))!=load_json(a.source/'static_connections.json'):raise ValueError('Ground normalization changed physical binding/static links')
    a.out.mkdir(exist_ok=False);placement=a.out/'placement';placement.mkdir();(placement/'packed.json').write_text(json.dumps(design,indent=2));shutil.copyfile(a.source/'placement/placement.json',placement/'placement.json')
    proof=prove(load_json(a.reference/'packed.json'),design,a.out/'cec',120)
    if not proof or not all(r['cec']['equivalent'] for r in proof):raise ValueError('Ground normalization CEC failed')
    pm=load_json(a.source/'placement/manifest.json');pm.update(netlist_sha256=sha(placement/'packed.json'),proof=dict(status='fixed_state_combinational_cec_proved',checks=proof),ground_padding_normalized=changed)
    (placement/'manifest.json').write_text(json.dumps(pm,indent=2))
    for name in ('routes.json','nets.json','all_nets.json','static_connections.json','clock_tree.json','hard_endpoint_audit.json','selected.json'):shutil.copyfile(a.source/name,a.out/name)
    meta.update(netlist_sha256=sha(placement/'packed.json'),proof=pm['proof'],ground_padding_normalized=changed,source_ground_repair_manifest_sha256=sha(a.source/'manifest.json'),producer_sha256=sha(Path(__file__)))
    (a.out/'manifest.json').write_text(json.dumps(meta,indent=2));shutil.copyfile(__file__,a.out/'repair_source.py.txt');print(json.dumps(dict(status=meta['status'],changed_inputs=changed,physical_binding_identical=True),indent=2))


if __name__=='__main__':main()
