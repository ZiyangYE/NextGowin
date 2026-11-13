"""Same-inference, proved physical screening of generic mapper recipes.

The baseline synthesis is never an input to a candidate. Every candidate uses
the retained own RTL/resource checkpoint and the exact original benchmark SDC.
The complete specialized mapper source and dependency hashes are retained.
"""
import argparse
import importlib.util
import json
from pathlib import Path
import re
import shutil
import sys
import time
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from run_abc_reproduction import sha
from map_resource_json import map_design
from nextgowin import materialize
from registered_benchmark import prove
from gowin_blif import read_blif
from run_general_synthesis_suite import physical,method_hashes


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--base',type=Path,required=True);ap.add_argument('--out',type=Path,required=True)
    ap.add_argument('--variant',choices=['lut4','syn3','sop','local'],required=True);ap.add_argument('--designs',nargs='+',required=True)
    args=ap.parse_args();out=args.out.resolve();out.mkdir(parents=True)
    source=(ROOT/'scripts/gw5a_arch_mapper.py').read_text()
    rewrite='&get -n; &syn2 -d -R 0; &b -d; &put'
    if args.variant=='lut4':
        assert source.count('for k in range(1,7)')==1
        source=source.replace('for k in range(1,7)','for k in range(1,5)').replace('max_cuts=8','max_cuts=1')
    else:
        sequence=rewrite+'; '+rewrite+'; '+rewrite if args.variant=='syn3' else rewrite+'; if -g -K 6; strash; '+rewrite if args.variant=='sop' else 'balance -d; rewrite; refactor; balance; rewrite; rewrite -z; balance; refactor -z; rewrite -z; balance -d'
        source,count=re.subn(r"^    preprocessing='[^']*'",'    preprocessing='+repr(sequence),source,flags=re.M);assert count==1
    path=out/'arch_mapper.py';path.write_text(source)
    spec=importlib.util.spec_from_file_location('variant_mapper',path);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    import gw5a_arch_mapper
    gw5a_arch_mapper.run=module.run
    result=dict(variant=args.variant,mapper_source_sha256=sha(path),dependencies=method_hashes(),base=str(args.base.resolve()),runs={})
    def save():(out/'manifest.json').write_text(json.dumps(result,indent=2)+'\n')
    base_manifest=json.loads((args.base/'manifest.json').read_text())
    for name in args.designs:
        p=out/name;p.mkdir();base=args.base/name;old=base_manifest['runs'][name];start=time.perf_counter();row={};result['runs'][name]=row;save()
        try:
            resources=base/'own/03_resources';staged=p/'03_resources';staged.mkdir()
            for f in ['register_choices.json','dsp_cells.v']:
                if (resources/f).exists():shutil.copyfile(resources/f,staged/f)
            row.update(resource_source_sha256=sha(resources/'resources.json'),sdc_sha256=sha(base/'clock.sdc'))
            map_design(resources/'resources.json',p/'04_mapping',10000,'arch_delay')
            materialize(p/'04_mapping/mapped.json',p/'05_netlist','top',base/'own/02_bridge/vendor_cells.v',register_alternatives=True)
            proof=p/'proof';proof.mkdir();shutil.copyfile(base/'reference.blif',proof/'reference.blif')
            row['proof']=prove(json.loads((p/'05_netlist/final.json').read_text()),read_blif(base/'reference.blif',max_inputs=11),proof)
            row['physical']=physical(p/'05_netlist/mapped.vg',base/'clock.sdc',p/'pnr',600)
            row.update(status='complete',vendor_fmax_mhz=old['vendor']['fmax_mhz'],ratio=row['physical']['fmax_mhz']/old['vendor']['fmax_mhz'])
            print(name,args.variant,'vendor',old['vendor']['fmax_mhz'],'own',row['physical']['fmax_mhz'],flush=True)
        except Exception as e:row.update(status='failed',error=repr(e));print(name,'FAILED',repr(e),flush=True)
        row['wall_seconds']=time.perf_counter()-start;save()


if __name__=='__main__':main()
