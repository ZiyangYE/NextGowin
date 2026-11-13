"""Own-coarse checkpoint -> delay reconstruction -> box-aware ABC9 -> CEC.

Experimental checkpoint run, not a fresh cold-runtime or superiority claim.
"""
import argparse
import json
from pathlib import Path
import shutil
import sys
import time
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from abc9_pre_rewrite import rewrite
from native_boundary_cec import prove
from lower_carry_only import lower
from nextgowin import materialize
from yosys_runtime import run,quote
from run_abc_reproduction import sha
from gowin_netlist_format import LIBRARY,EXPECTED
from gowin_cones import masked
from verific_bridge import units
import re


def native_interfaces(path,kinds):
    if sha(LIBRARY)!=EXPECTED:raise ValueError('Native functional library changed')
    text=LIBRARY.read_text(encoding='utf-8');rows=[]
    for name,(a,b) in units(text).items():
        if name not in kinds:continue
        body=masked(text[a:b]);head=body[:body.index(';')+1]
        declarations=re.findall(r'^\s*(?:parameter|localparam|input|output|inout)\b[^;]*;',body,re.M)
        rows.append('(* blackbox=1 *) '+head+'\n'+'\n'.join(declarations)+'\nendmodule\n')
    path.write_text('\n'.join(rows));return path


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--source',type=Path,required=True);ap.add_argument('--out',type=Path,required=True)
    ap.add_argument('--wide',action='store_true');ap.add_argument('--prepared',type=Path);ap.add_argument('--timing-models',type=Path);args=ap.parse_args();out=args.out.resolve();out.mkdir(parents=True);source=args.source.resolve();start=time.perf_counter()
    parent=json.loads((source/'manifest.json').read_text());config=parent['config'];top=json.loads((source/'01_frontend/manifest.json').read_text())['top']
    if not parent['frontend_only_vendor']:raise ValueError('Requires own coarse inference')
    common=f'-top {top} -family gw5a -strict-gw5a-dffs -noflatten -noiopads'+('' if args.wide else ' -nowidelut')
    p=out/'04_mapping';p.mkdir();resources=out/'03_resources';resources.mkdir()
    shutil.copyfile(source/'03_resources/dsp_cells.v',resources/'dsp_cells.v')
    timing_read='read_verilog -overwrite -specify -lib '+quote(args.timing_models) if args.timing_models else 'read_verilog -overwrite -specify -lib +/gowin/cells_sim.v'
    if args.prepared:
        for name in ('before_hard.json','before.json','rewritten.json','comparison_lowering.json','interfaces.v'):
            shutil.copyfile(args.prepared/name,p/name)
        reference=p/'before.json';proofs=[]
    else:
        run(['read_rtlil '+quote(source/'03_resources/inferred.il'),
         'read_verilog -overwrite -lib '+quote(source/'02_bridge/vendor_cells.v'),
         'read_verilog -overwrite -lib '+quote(resources/'dsp_cells.v'),timing_read,
         'synth_gowin '+common+' -run map_gates:map_luts','blackbox =A:whitebox',
         'write_verilog -blackboxes '+quote(p/'interfaces.v'),'write_json '+quote(p/'before_hard.json')],p,'prepare',900)
        d,lowering=lower(json.loads((p/'before_hard.json').read_text()));(p/'comparison_lowering.json').write_text(json.dumps(lowering,indent=2)+'\n')
        reference=p/'before.json';reference.write_text(json.dumps(d)+'\n');print('Carry-only ALUs removed',lowering['alu_cells_removed'],flush=True)
        d,proofs=rewrite(d,p/'rewrite');(p/'rewritten.json').write_text(json.dumps(d)+'\n');print('Delay rewrite CEC passed',flush=True)
    from map_resource_json import FUNCTIONS
    rebuilt=json.loads((p/'rewritten.json').read_text())
    active=[n for n,m in rebuilt['modules'].items() if any(c['type'] in FUNCTIONS for c in m.get('cells',{}).values())]
    used={c['type'] for m in rebuilt['modules'].values() if not int(m.get('attributes',{}).get('blackbox','0'),2) for c in m.get('cells',{}).values()}
    supplied=set(re.findall(r'\bmodule\s+(\\\S+|[A-Za-z_$][\w$]*)',masked((source/'02_bridge/vendor_cells.v').read_text(encoding='utf-8'))))
    native_library=native_interfaces(p/'native_interfaces.v',used-supplied)
    print('ABC9 user modules',active,flush=True)
    run(['read_json '+quote(p/'rewritten.json'),
         'setattr -mod -unset abc9_box =*','setattr -mod -unset abc9_box_id =*','setattr -mod -unset abc9_lut =*','setattr -mod -unset abc9_flop =*',
         'read_verilog -overwrite -lib '+quote(native_library),
         'read_verilog -overwrite -lib '+quote(source/'02_bridge/vendor_cells.v'),
         'read_verilog -overwrite -lib '+quote(resources/'dsp_cells.v'),timing_read,
         'sort','read_verilog -icells -lib -specify +/abc9_model.v','select '+' '.join(active)+' A:abc9_lut A:abc9_box A:abc9_box_id A:abc9_flop',
         'abc9 -maxlut '+('8' if args.wide else '4')+' -W 500','techmap -map +/gowin/cells_map.v',
         'opt_lut_ins -tech gowin','clean','select *','bufnorm -conn','blackbox =A:whitebox','write_json '+quote(p/'mapped.json')],p,'abc9',900)
    proof=prove(json.loads(reference.read_text()),json.loads((p/'mapped.json').read_text()),p/'native_proof',300)
    print('All hard-boundary actual JSON CEC passed',flush=True)
    materialize(p/'mapped.json',out/'05_netlist',top,source/'02_bridge/vendor_cells.v',config.get('max_logic_cells'),config.get('net_aliases',{}))
    r=dict(status='emitted',scope=__doc__,source_coarse_sha256=sha(source/'03_resources/inferred.il'),source_flow_sha256=sha(source/'manifest.json'),
           timing_model_sha256=sha(args.timing_models) if args.timing_models else None,rewrite_proofs=proofs,native_proofs=proof,prepared_reuse_sha256=sha(args.prepared/'rewritten.json') if args.prepared else None,prepare_map_emit_seconds=time.perf_counter()-start,
           output_netlist_sha256=sha(out/'05_netlist/mapped.vg'),implementation_sha256={str(f.relative_to(ROOT)):sha(f) for f in [Path(__file__).resolve(),ROOT/'experiments/abc9_pre_rewrite.py',ROOT/'scripts/native_boundary_cec.py']})
    (out/'manifest.json').write_text(json.dumps(r,indent=2)+'\n');print('Prepared in',r['prepare_map_emit_seconds'],flush=True)


if __name__=='__main__':main()
