"""Admit only mapped user modules; keep opaque IP and physical interfaces exact.

ABC9's global buffer normalization can change unselected hierarchical inouts.
Those modules are not optimization candidates and are restored from the input.
The combined actual graph must then pass CEC and normal native I/O insertion.
"""
import argparse
import copy
import json
from pathlib import Path
import sys
import time
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from map_resource_json import FUNCTIONS
from native_boundary_cec import prove
from nextgowin import materialize
from run_abc_reproduction import sha


def resolve_inputs(design):
    count=0
    for m in design['modules'].values():
        for c in m.get('cells',{}).values():
            for p,bs in c['connections'].items():
                if c.get('port_directions',{}).get(p) not in ('input','inout'):continue
                for i,b in enumerate(bs):
                    if b in ('x','z'):bs[i]='0';count+=1
    return count


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--source',type=Path,required=True);ap.add_argument('--candidate',type=Path,required=True);args=ap.parse_args()
    source=args.source.resolve();out=args.candidate.resolve();p=out/'04_mapping';start=time.perf_counter()
    before=json.loads((p/'before.json').read_text());rewritten=json.loads((p/'rewritten.json').read_text());mapped=json.loads((p/'mapped.json').read_text())
    active=[n for n,m in rewritten['modules'].items() if any(c['type'] in FUNCTIONS for c in m.get('cells',{}).values())]
    combined=copy.deepcopy(before)
    for name in active:
        combined['modules'][name]=copy.deepcopy(mapped['modules'][name])
        combined['modules'][name]['attributes']=copy.deepcopy(before['modules'][name].get('attributes',{}))
    zeros=[resolve_inputs(before),resolve_inputs(combined)]
    reference=p/'scope_reference.json';reference.write_text(json.dumps(before)+'\n')
    target=p/'scope_restored.json';target.write_text(json.dumps(combined)+'\n')
    proof=prove(before,combined,p/'scope_proof',300);print('Actual scoped CEC passed',active,flush=True)
    config=json.loads((source/'manifest.json').read_text())['config'];top=json.loads((source/'01_frontend/manifest.json').read_text())['top']
    emit=out/'05_scoped_netlist'
    materialize(target,emit,top,source/'02_bridge/vendor_cells.v',config.get('max_logic_cells'),config.get('net_aliases',{}))
    r=dict(status='emitted',scope='Checkpoint experiment; only intended user modules changed. Native IP, primitive interfaces and original board constraints retained. No cold-runtime or superiority claim.',
           mapped_user_modules=active,undefined_input_zero_fill=zeros,proof=proof,prepare_seconds=time.perf_counter()-start,
           reference_sha256=sha(reference),restored_sha256=sha(target),netlist_sha256=sha(emit/'mapped.vg'),netlist=str(emit/'mapped.vg'))
    (out/'scoped_manifest.json').write_text(json.dumps(r,indent=2)+'\n');print('Scoped netlist emitted',flush=True)


if __name__=='__main__':main()
