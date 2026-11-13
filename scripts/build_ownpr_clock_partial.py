"""Merge initial dedicated-clock requirements with own logic/data configuration.

Only a partial configuration is emitted. The ordinary-clock PIPs are removed
from the projection before adding the dedicated tree; no full image is made.
"""
import argparse,hashlib,json
from pathlib import Path
from ownpr_device import Device,ROOT
from ownpr_clock_graph import ClockGraph
from ownpr_clock_configuration import ClockChoices
from ownpr_configuration import build
from audit_native_clock_encoding import audit


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--out',type=Path,required=True);ap.add_argument('--routes',type=Path,default=ROOT/'results/ownpr_int2float_route_008');ap.add_argument('--placement',type=Path,default=ROOT/'results/ownpr_int2float_place_004');args=ap.parse_args();out=args.out.resolve();out.mkdir(exist_ok=False)
    candidate=ROOT/'results/ownpr_clock_tree_001';route_folder=args.routes.resolve();placement=args.placement.resolve()
    tree_path=candidate/'clock_tree.json';tree=json.loads(tree_path.read_text());meta=json.loads((candidate/'manifest.json').read_text())
    calibrated=audit(ROOT/'results/native_clock_encoding_001',tree_path)
    if calibrated['status']!='clock_candidate_bits_matched':raise ValueError('Clock bit mapping calibration failed')
    rm=json.loads((route_folder/'manifest.json').read_text())
    if (meta['placement_sha256']!=rm['placement_sha256'] or hashlib.sha256((placement/'placement.json').read_bytes()).hexdigest()!=rm['placement_sha256']
            or hashlib.sha256((placement/'packed.json').read_bytes()).hexdigest()!=rm['netlist_sha256']):
        raise ValueError('Clock and data placements differ')
    reference=json.loads((ROOT/'results/ownpr_int2float_place_004/packed.json').read_text())['modules']['top'];current=json.loads((placement/'packed.json').read_text())['modules']['top']
    fixed=lambda m:{n:c for n,c in m['cells'].items() if not c['type'].startswith('LUT')}
    oldclocks=[n for n in json.loads((ROOT/'results/ownpr_int2float_route_008/nets.json').read_text()) if n.get('clock')]
    newclocks=[n for n in json.loads((route_folder/'nets.json').read_text()) if n.get('clock')]
    if fixed(reference)!=fixed(current) or reference['ports']!=current['ports'] or oldclocks!=newclocks:raise ValueError('Frozen clock/interface changed')
    clock_names=[n['name'] for n in json.loads((route_folder/'nets.json').read_text()) if n.get('clock')]
    if len(clock_names)!=1 or meta['clock_policy_change']['after']!='AUTO':raise ValueError('Unsupported clock contract')
    routes=json.loads((route_folder/'routes.json').read_text());graph=ClockGraph(ROOT/'results/native_clock_inventory_005');device=Device();codec=ClockChoices(device,graph)
    requirements,clock_check=codec.requirements(tree);ordinary=[]
    for edge in clock_check['edges']:
        if edge['kind']=='ordinary_switch':
            sr,sc,si=edge['source'];_,_,di=edge['destination'];ordinary.append([sr-1,sc-1,device.names[si],device.names[di]])
    projection=dict(status='incomplete_clock_encoding_projection',nets={name:dict(pips=ordinary if name==clock_names[0] else row['pips']) for name,row in routes['nets'].items()},
        scope='Only an input to the partial configuration checker, not a routed graph.')
    projected=out/'projection';projected.mkdir();(projected/'routes.json').write_text(json.dumps(projection))
    partial=build(placement,projected);values={(b['tile'][0],b['tile'][1],b['local_bit'][0],b['local_bit'][1]):b['value'] for b in partial['bits']}
    conflicts=list(partial['conflicts'])+list(clock_check['conflicts'])
    for bit,value in requirements.items():
        if bit in values and values[bit]!=value:conflicts.append(dict(bit=bit,logic_data=values[bit],clock=value))
        values[bit]=value
    partial.update(status='partial_clock_logic_conflicting' if conflicts else 'partial_clock_logic_consistent',
        bits=[dict(tile=[r,c],local_bit=[y,x],value=v) for (r,c,y,x),v in sorted(values.items())],conflicts=conflicts,
        clock_bits_calibration=calibrated,clock_contract=meta['clock_policy_change'],
        original_cst_sha256=meta['original_cst_sha256'],proposed_cst_sha256=meta['proposed_cst_sha256'],sdc_sha256=meta['sdc_sha256'],
        source_clock_tree_sha256=hashlib.sha256(tree_path.read_bytes()).hexdigest(),
        scope='Own logic/data requirements plus initial dedicated-clock configuration. I/O, source modes, GSR, defaults, complete constraints and full hardware image remain unverified.')
    (out/'partial_configuration.json').write_text(json.dumps(partial,indent=2))
    print(partial['status'],'bits',len(values),'conflicts',len(conflicts))
    if conflicts:raise SystemExit(1)


if __name__=='__main__':main()
