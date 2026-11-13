"""Experimental local SDPB induction against the hash-pinned SDK reference.

The SDK frontend normalization rewrites contiguous write concatenations only.
No full-design admission is implied by this local result. Monolithic SAT remains
expensive; prefer check_sdpb_transition_relation.py for the small obligations.
"""
import argparse
import json
from pathlib import Path
import time

from ownpr_sdpb_formal import word_model, normalized_packed_reference
from yosys_runtime import quote, run
from workspace_budget import reserve
from ownpr_image_storage import sha


def main():
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True)
    p.add_argument('--width',type=int,choices=(1,2,4,8,16,32),required=True)
    p.add_argument('--mode',type=int,choices=(0,1),required=True)
    p.add_argument('--timeout',type=int,default=60)
    a=p.parse_args();out=a.output.resolve();reserve(Path(__file__).resolve().parents[1],150_000_000)
    out.mkdir(parents=True,exist_ok=False)
    ref,changes=normalized_packed_reference();(out/'reference.v').write_text(ref)
    # Internal correspondence points represent the SAME physical state. They
    # are proof obligations, not assumed equality without a base/induction check.
    aliases='''
wire [31:0] bp_reg = {{(32-BIT_WIDTH_0){1'b0}},supported.bp_reg};
wire [31:0] pl_reg = {{(32-BIT_WIDTH_0){1'b0}},supported.pl_reg};
'''
    (out/'word.v').write_text(word_model().replace('endmodule',aliases+'\nendmodule'))
    cmds=[]
    for file,label in [('reference.v','gold'),('word.v','gate')]:
        cmds += ['read_verilog '+quote(out/file),
                 f'chparam -set BIT_WIDTH_0 {a.width} -set BIT_WIDTH_1 {a.width} -set READ_MODE {a.mode} SDPB',
                 'hierarchy -check -top SDPB','proc','flatten','opt','memory -nomap','memory_map','opt','check -assert',
                 'clk2fflogic','opt',f'rename SDPB {label}',f'design -stash {label}']
    cmds += ['design -load gold','write_json '+quote(out/'gold.json'),
             'design -load gate','write_json '+quote(out/'gate.json')]
    proof=['read_json '+quote(out/'gold.json'),'read_json '+quote(out/'gate_alias.json'),
             'design -save original_pair',
             'miter -equiv -make_assert -flatten gold gate original_base',
             'hierarchy -check -top original_base','opt',
             'sat -seq 4 -set-init-undef -set-def-inputs -prove-asserts -verify',
             'design -load original_pair',
             'equiv_make gold gate equiv','hierarchy -top equiv','opt_clean',
             'equiv_induct -seq 2','equiv_status -assert']
    begin=time.time();report={'width':a.width,'read_mode':a.mode,'scope':'local SDK-normalized SDPB versus word model; inactive GSR; SYNC; default all-zero INIT',
                              'normalization_changes':changes,'original_pair_initial_base_steps':4,
                              'induction_steps':2,'passed':False}
    try:
        report['lowering']=run(cmds,out,'lower',timeout=a.timeout)
        gate=json.loads((out/'gate.json').read_text());module=gate['modules']['gate'];bits=[]
        # Add a wire alias AFTER lowering. Adding all words as HDL read ports
        # first produces millions of useless muxes before memory optimization.
        for i in range(16384//a.width):
            word=module['netnames'][f'supported.words[{i}]']['bits']
            if len(word)!=a.width:raise ValueError('State word width changed')
            bits.extend(word)
        if len(bits)!=16384 or len(set(bits))!=16384 or not all(isinstance(b,int) for b in bits):
            raise ValueError('State correspondence is not a bit bijection')
        if 'ram_MEM' in module['netnames']:raise ValueError('State alias already exists')
        module['netnames']['ram_MEM']={'hide_name':0,'bits':bits,'attributes':{'keep':'1'}}
        (out/'gate_alias.json').write_text(json.dumps(gate))
        report['alias_source_sha256']=sha(out/'gate.json')
        report['alias_sha256']=sha(out/'gate_alias.json')
        report['invocation']=run(proof,out,'prove',timeout=max(1,a.timeout-(time.time()-begin)))
        report['passed']=True
    except Exception as e:report['error']=type(e).__name__+': '+str(e)
    report['wall_seconds']=time.time()-begin
    (out/'result.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report,indent=2))
    if not report['passed']:raise SystemExit(1)


if __name__=='__main__':main()
