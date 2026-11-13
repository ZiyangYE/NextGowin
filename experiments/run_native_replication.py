import argparse
import json
from pathlib import Path
import shutil
import sys
import time
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from replicate_native_logic import replicate,prove
from nextgowin import materialize
from run_abc_reproduction import sha


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--source',type=Path,required=True);ap.add_argument('--out',type=Path,required=True)
    args=ap.parse_args();source=args.source.resolve();out=args.out.resolve();out.mkdir(parents=True);start=time.perf_counter()
    parent=json.loads((source/'manifest.json').read_text())
    if not parent.get('frontend_only_vendor') or parent['own_mapping_source'].startswith('Vendor'):raise ValueError('Candidate must originate in own resource synthesis')
    before=json.loads((source/'05_netlist/final.json').read_text());after,record=replicate(before)
    (out/'replication.json').write_text(json.dumps(record,indent=2)+'\n');print('LUT copies',record['clones'],flush=True)
    proof=prove(before,after,out/'proof');print('Boundary CEC passed',flush=True)
    mapping=out/'04_mapping';mapping.mkdir();p=mapping/'mapped.json';p.write_text(json.dumps(after)+'\n')
    resources=out/'03_resources';resources.mkdir()
    if (source/'03_resources/dsp_cells.v').exists():shutil.copyfile(source/'03_resources/dsp_cells.v',resources/'dsp_cells.v')
    materialize(p,out/'05_netlist',json.loads((source/'01_frontend/manifest.json').read_text())['top'],source/'02_bridge/vendor_cells.v',optimize_resources=False)
    r=dict(status='emitted',scope='Own checkpoint experiment; no cold end-to-end runtime or general superiority claim.',
           source_flow_manifest_sha256=sha(source/'manifest.json'),source_json_sha256=sha(source/'05_netlist/final.json'),
           output_netlist_sha256=sha(out/'05_netlist/mapped.vg'),replication=record,proof=proof,
           preparation_wall_seconds=time.perf_counter()-start,upstream_cold_stage_seconds=parent['total_stage_wall_seconds'],
           implementation_sha256={str(p.relative_to(ROOT)):sha(p) for p in [Path(__file__).resolve(),ROOT/'experiments/replicate_native_logic.py']})
    (out/'manifest.json').write_text(json.dumps(r,indent=2)+'\n');print('Prepared in',r['preparation_wall_seconds'],flush=True)


if __name__=='__main__':main()
