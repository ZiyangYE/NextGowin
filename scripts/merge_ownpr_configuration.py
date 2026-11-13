"""Merge ordinary, arithmetic and separately calibrated clock requirements."""
import argparse,json,hashlib
from pathlib import Path
from ownpr_configuration import build
from ownpr_device import Device,RoutingGraph,ROOT
from ownpr_clock_graph import ClockGraph
from ownpr_clock_configuration import ClockChoices
from ownpr_clock_partition import partition
from audit_native_clock_encoding import audit as audit_clock


def merge(placement,routes,clock_capture):
    placement=Path(placement);routes=Path(routes);clock_capture=Path(clock_capture);treefile=routes/'clock_tree.json'
    tree=json.loads(treefile.read_text());check=audit_clock(clock_capture,treefile)
    if check['status']!='clock_candidate_bits_matched':raise ValueError('Clock encoding calibration did not match')
    meta=json.loads((routes/'manifest.json').read_text())
    if (meta['status']!='routed' or meta['netlist_sha256']!=hashlib.sha256((placement/'packed.json').read_bytes()).hexdigest()
            or meta['placement_sha256']!=hashlib.sha256((placement/'placement.json').read_bytes()).hexdigest()):raise ValueError('Configuration inputs differ from completed routing')
    device=Device();ordinary,_=partition(RoutingGraph(device,ROOT/'results/native_rrg_001'),json.loads((routes/'all_nets.json').read_text()),tree)
    if ordinary!=json.loads((routes/'nets.json').read_text()):raise ValueError('Clock/data partition mismatch')
    result=build(placement,routes);bits={(tuple(r['tile']+r['local_bit'])):r['value'] for r in result['bits']}
    additions,clock_info=ClockChoices(device,ClockGraph.for_tree(tree)).requirements(tree)
    conflicts=list(result['conflicts'])+clock_info['conflicts']
    for bit,value in additions.items():
        if bit in bits and bits[bit]!=value:conflicts.append(dict(bit=bit,ordinary=bits[bit],clock=value))
        bits[bit]=value
    result.update(status='combined_partial_conflicting' if conflicts else 'combined_partial_consistent',
        bits=[dict(tile=list(bit[:2]),local_bit=list(bit[2:]),value=value) for bit,value in sorted(bits.items())],conflicts=conflicts,
        clock_configuration_audit=check,clock_tree_sha256=hashlib.sha256(treefile.read_bytes()).hexdigest(),
        scope='Own LUT/FF/ALU/data/clock configuration requirements. Complete physical modes, carry timing and I/O/bank/GSR final encoding remain outside this partial artifact.')
    return result


if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('--placement',type=Path,required=True);ap.add_argument('--routes',type=Path,required=True);ap.add_argument('--clock-capture',type=Path,required=True);ap.add_argument('--out',type=Path,required=True);a=ap.parse_args();r=merge(a.placement,a.routes,a.clock_capture)
    a.out.parent.mkdir(parents=True,exist_ok=True);a.out.write_text(json.dumps(r,indent=2));print(r['status'],'bits',len(r['bits']),'conflicts',len(r['conflicts']))
    if r['conflicts']:raise SystemExit(1)
