"""Fixed area-mode EqMap development screen; every failure remains visible."""
import argparse
import json
import math
from pathlib import Path
import shutil
import subprocess
import sys
import time
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from run_abc_reproduction import sha
from eqmap_cut_adapter import optimize,BINARY,SOURCE

DESIGNS={
    'ctrl':'arch_variant_lut4_001','priority':'arch_variant_lut4_001','router':'arch_variant_lut4_001',
    'int2float':'arch_variant_lut4_002','cavlc':'arch_variant_lut4_002','dec':'arch_variant_lut4_002',
    'i2c':'arch_variant_lut4_002','sin':'arch_variant_lut4_002',
    'bar':'arch_variant_lut4_003','multiplier':'arch_variant_lut4_003'}


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--out',type=Path,required=True);args=ap.parse_args()
    out=args.out.resolve();out.mkdir(parents=True,exist_ok=False)
    files=[Path(__file__).resolve(),ROOT/'experiments/eqmap_cut_adapter.py',ROOT/'experiments/run_eqmap_physical.py']
    files+=list((ROOT/'scripts').glob('*.py'))+[ROOT/'configs/gw5a_arch_cost_v2.json']
    hashes={p.relative_to(ROOT).as_posix():sha(p) for p in files}
    for name in hashes:
        target=out/'method_source'/name;target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(ROOT/name,target)
    base=ROOT/'results/general_synthesis_suite_012';baseline=json.loads((base/'manifest.json').read_text())
    record=dict(status='running',designs_predeclared=list(DESIGNS),runs={},method_sha256=hashes,
        eqmap_binary_sha256=sha(BINARY),scope='All designs were previously observed; development screen only. Own-resource checkpoints, unchanged benchmark SDC. No cold-runtime or general-superiority claim.')
    def save():(out/'manifest.json').write_text(json.dumps(record,indent=2)+'\n')
    save()
    for name,variant in DESIGNS.items():
        p=out/name;p.mkdir();start=time.perf_counter();row=dict(status='running');record['runs'][name]=row;save()
        try:
            if any(sha(ROOT/n)!=h for n,h in hashes.items()):raise ValueError('Method changed during cohort')
            source=ROOT/'results'/variant/name/'04_mapping/module_0/mapper/wide_logic.blif'
            row['source_mapped_cut_sha256']=sha(source)
            report,_=optimize(source,p/'eqmap')
            row['eqmap']=dict(wall_seconds=report['wall_seconds'],proof=report['proof'])
            with (p/'physical_runner.log').open('w') as log:
                subprocess.run([sys.executable,str(ROOT/'experiments/run_eqmap_physical.py'),'--source',str(base/name),
                    '--eqmap',str(p/'eqmap'),'--out',str(p/'physical')],stdout=log,stderr=subprocess.STDOUT,check=True,timeout=900)
            checked=json.loads((p/'physical/manifest.json').read_text());own=checked['physical'];vendor=baseline['runs'][name]['vendor']
            row.update(status='complete',own_fmax_mhz=own['fmax_mhz'],vendor_fmax_mhz=vendor['fmax_mhz'],
                       own_violations=own['violated_endpoints'],vendor_violations=vendor['violated_endpoints'],
                       own_timing_closed=own['timing_closed'],vendor_timing_closed=vendor['timing_closed'],ratio=own['fmax_mhz']/vendor['fmax_mhz'])
            if any(sha(ROOT/n)!=h for n,h in hashes.items()):raise ValueError('Method changed during design')
            print(name,row['own_fmax_mhz'],row['vendor_fmax_mhz'],row['own_violations'],flush=True)
        except Exception as error:row.update(status='failed',error=repr(error));print(name,'FAILED',repr(error),flush=True)
        row['wall_seconds']=time.perf_counter()-start;save()
    complete=[r for r in record['runs'].values() if r['status']=='complete']
    record.update(status='complete' if len(complete)==len(DESIGNS) else 'incomplete_or_failed',
        summary=dict(completed=len(complete),declared=len(DESIGNS),superiority_established=False,
            geomean_fmax_ratio=math.exp(sum(math.log(r['ratio']) for r in complete)/len(complete)) if complete else None,
            timing_valid_wins=sum(r['ratio']>1 and r['own_timing_closed'] and r['vendor_timing_closed'] for r in complete)))
    save();print(record['summary'])


if __name__=='__main__':main()
