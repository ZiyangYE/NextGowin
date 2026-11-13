"""Normalize ABC9 wire buffers, prove every native hard boundary, emit VG."""
import argparse
import json
from pathlib import Path
import shutil
import sys
import time
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from native_boundary_cec import prove
from nextgowin import materialize
from yosys_runtime import run,quote
from run_abc_reproduction import sha


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--source',type=Path,required=True);ap.add_argument('--candidate',type=Path,required=True);args=ap.parse_args()
    source=args.source.resolve();out=args.candidate.resolve();p=out/'04_mapping';start=time.perf_counter()
    # Keep the failed proof and its exact inputs for review.
    for name in ('before','mapped'):
        raw=p/(name+'_before_alias_fix.json')
        if not raw.exists():shutil.copyfile(p/(name+'.json'),raw)
        run(['read_json '+quote(raw),'bufnorm -conn','setundef -undriven -params -zero','blackbox =A:whitebox',
             'write_json '+quote(p/(name+'_normalized.json'))],p,'normalize_'+name,300)
    reference=p/'before_normalized.json';target=p/'mapped_normalized.json'
    trial=0
    while (p/('normalized_proof_'+str(trial))).exists():trial+=1
    proof=prove(json.loads(reference.read_text()),json.loads(target.read_text()),p/('normalized_proof_'+str(trial)),300)
    print('Actual native boundary CEC passed',flush=True)
    config=json.loads((source/'manifest.json').read_text())['config'];top=json.loads((source/'01_frontend/manifest.json').read_text())['top']
    materialize(target,out/'05_netlist',top,source/'02_bridge/vendor_cells.v',config.get('max_logic_cells'),config.get('net_aliases',{}))
    r=dict(status='emitted',scope='Own coarse checkpoint experiment; wire aliases normalized and the existing zero-fill policy applied symmetrically. No cold-runtime or superiority claim.',
           source_coarse_sha256=sha(source/'03_resources/inferred.il'),reference_sha256=sha(reference),mapped_sha256=sha(target),proof=proof,
           finish_seconds=time.perf_counter()-start,netlist_sha256=sha(out/'05_netlist/mapped.vg'))
    (out/'manifest.json').write_text(json.dumps(r,indent=2)+'\n');print('Emitted',flush=True)


if __name__=='__main__':main()
