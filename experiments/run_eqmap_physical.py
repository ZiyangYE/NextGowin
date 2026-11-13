"""Admit a combinational EqMap checkpoint through real-cell/state/physical proofs."""
import argparse
import json
from pathlib import Path
import shutil
import sys
import time
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from map_resource_json import extract,replace,readback
from gowin_blif import read_blif
from gw5a_functional_map import lower
from nextgowin import materialize
from registered_benchmark import prove
from run_general_synthesis_suite import physical
from run_abc_reproduction import sha,cec


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--source',type=Path,required=True)
    ap.add_argument('--eqmap',type=Path,required=True);ap.add_argument('--out',type=Path,required=True)
    args=ap.parse_args();source=args.source.resolve();eq=args.eqmap.resolve();out=args.out.resolve();out.mkdir(parents=True)
    start=time.perf_counter();r=dict(status='running',scope='Own-resource checkpoint experiment, not cold full synthesis or a superiority claim.',
        source_resource_sha256=sha(source/'own/03_resources/resources.json'),eqmap_manifest_sha256=sha(eq/'manifest.json'))
    def save():(out/'manifest.json').write_text(json.dumps(r,indent=2)+'\n')
    save()
    try:
        design=json.loads((source/'own/03_resources/resources.json').read_text())
        candidates=[(n,m,extract(m)) for n,m in design['modules'].items()]
        candidates=[row for row in candidates if row[2]]
        if len(candidates)!=1:raise ValueError('This screening runner requires one Boolean user module')
        name,module,cut=candidates[0]
        cost=json.loads((ROOT/'configs/gw5a_arch_cost_v2.json').read_text())
        legal,architecture=lower(read_blif(eq/'readback.blif',max_inputs=6),{n:0 for n in cut['inputs']},
                                 lut_ps=cost['lut_ps'],cut_wire_ps=cost['cut_wire_ps'])
        actual=replace(module,cut,legal);p=out/'04_mapping';p.mkdir()
        (p/'source.blif').write_text(cut['blif']);(p/'actual.blif').write_text(readback(actual,cut))
        r['actual_json_proof']=cec(ROOT/'build/abc/_TEST/abc.exe',p/'source.blif',p/'actual.blif',p/'cec.log',180)
        if not r['actual_json_proof']['equivalent']:raise ValueError('Actual EqMap native LUT INIT proof failed')
        design['modules'][name]=actual;(p/'mapped.json').write_text(json.dumps(design)+'\n')
        resources=out/'03_resources';resources.mkdir()
        for f in ('register_choices.json','dsp_cells.v'):
            if (source/'own/03_resources'/f).exists():shutil.copyfile(source/'own/03_resources'/f,resources/f)
        materialize(p/'mapped.json',out/'05_netlist','top',source/'own/02_bridge/vendor_cells.v',register_alternatives=True)
        proof=out/'proof';proof.mkdir()
        r['sequential_contract_proof']=prove(json.loads((out/'05_netlist/final.json').read_text()),
            read_blif(source/'reference.blif',max_inputs=11),proof)
        r['physical']=physical(out/'05_netlist/mapped.vg',source/'clock.sdc',out/'pnr',600)
        r.update(status='complete',architecture=architecture)
    except Exception as e:r.update(status='failed',error=repr(e))
    r['wall_seconds']=time.perf_counter()-start;save()
    print({k:v for k,v in r.items() if k not in ('architecture','sequential_contract_proof','actual_json_proof','physical')})
    if 'physical' in r:print({k:r['physical'][k] for k in ('fmax_mhz','violated_endpoints','timing_closed')})
    if r['status']!='complete':raise SystemExit(1)


if __name__=='__main__':main()
