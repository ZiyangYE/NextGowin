"""Physical ABC9 baseline on the same retained own RTL/coarse inference."""
import argparse
import json
from pathlib import Path
import shutil
import sys
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from yosys_runtime import run,quote
from nextgowin import materialize
from registered_benchmark import prove
from gowin_blif import read_blif
from run_general_synthesis_suite import physical
from run_abc_reproduction import sha


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--base',type=Path,required=True);ap.add_argument('--out',type=Path,required=True)
    ap.add_argument('--designs',nargs='+',required=True);ap.add_argument('--wide',action='store_true');ap.add_argument('--rewrite',action='store_true');args=ap.parse_args()
    out=args.out.resolve();out.mkdir(parents=True);result=dict(wide=args.wide,rewrite=args.rewrite,runs={});baseline=json.loads((args.base/'manifest.json').read_text())
    for name in args.designs:
        p=out/name;p.mkdir();base=args.base/name;row={};result['runs'][name]=row
        try:
            common='-top top -family gw5a -strict-gw5a-dffs -noflatten -noiopads'+('' if args.wide else ' -nowidelut')
            stage=p/'04_mapping';stage.mkdir()
            row['mapping']=run(['read_rtlil '+quote(base/'own/03_resources/inferred.il'),
                'read_verilog -overwrite -specify -lib +/gowin/cells_sim.v','read_verilog -overwrite -specify -lib +/gowin/cells_xtra_gw5a.v',
                'synth_gowin '+common+' -run map_gates:map_luts',
                'blackbox =A:whitebox','write_json '+quote(stage/'before.json')],stage,'prepare',600)
            candidate=stage/'before.json'
            if args.rewrite:
                from abc9_pre_rewrite import rewrite
                d,proofs=rewrite(json.loads(candidate.read_text()),stage/'rewrite');candidate=stage/'rewritten.json';candidate.write_text(json.dumps(d)+'\n');row['rewrite_proofs']=proofs
            row['abc9']=run(['read_json '+quote(candidate),
                'read_verilog -overwrite -specify -lib +/gowin/cells_sim.v','read_verilog -overwrite -specify -lib +/gowin/cells_xtra_gw5a.v',
                'synth_gowin '+common+' -run map_luts:map_cells',
                'techmap -map +/gowin/cells_map.v','opt_lut_ins -tech gowin','clean','blackbox =A:whitebox',
                'write_json '+quote(stage/'mapped.json')],stage,'abc9',600)
            materialize(stage/'mapped.json',p/'05_netlist','top',base/'own/02_bridge/vendor_cells.v')
            proof=p/'proof';proof.mkdir();shutil.copyfile(base/'reference.blif',proof/'reference.blif')
            row['proof']=prove(json.loads((p/'05_netlist/final.json').read_text()),read_blif(base/'reference.blif',max_inputs=11),proof)
            row['physical']=physical(p/'05_netlist/mapped.vg',base/'clock.sdc',p/'pnr',600)
            row.update(status='complete',vendor_fmax_mhz=baseline['runs'][name]['vendor']['fmax_mhz'])
            print(name,'abc9','wide' if args.wide else 'lut4','vendor',row['vendor_fmax_mhz'],'own',row['physical']['fmax_mhz'],flush=True)
        except Exception as e:row.update(status='failed',error=repr(e));print(name,'FAILED',repr(e),flush=True)
        (out/'manifest.json').write_text(json.dumps(result,indent=2)+'\n')


if __name__=='__main__':main()
