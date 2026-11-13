"""Compare all mapped helper geometries against pinned native DSP models."""
import argparse
import json
import os
from pathlib import Path
import subprocess
from gw5a_dsp import prepare
from gowin_netlist_format import LIBRARY,EXPECTED
from yosys_runtime import SUITE


def verify(source,out):
    out.mkdir(parents=True,exist_ok=False)
    _,record,gold,gate=prepare(source.read_text());rows=record['multipliers']
    (out/'design.v').write_text(gold+'\n'+gate)
    declarations=[];checks=[];updates=[]
    for i,r in enumerate(rows):
        a=r['a_width'];b=r['b_width'];w=r['output_width'];n=r['module']
        declarations += [f'reg [{a-1}:0] a{i}; reg [{b-1}:0] b{i}; wire [{w-1}:0] g{i},n{i};',
            f'gold_{n} g_{i}(a{i},b{i},g{i}); gate_{n} n_{i}(a{i},b{i},n{i});']
        updates.append(f'a{i} = seed; seed=seed*32\'d1664525+32\'d1013904223; b{i} = seed;')
        # First 16 cases systematically cover zero, maximum and sign corners.
        updates.append(f'if(k<16) begin a{i}=(k%4==0)?0:(k%4==1)?{{{a}{{1\'b1}}}}:(k%4==2)?({a}\'d1<<{a-1}):(({a}\'d1<<{a-1})-1); b{i}=(k/4==0)?0:(k/4==1)?{{{b}{{1\'b1}}}}:(k/4==2)?({b}\'d1<<{b-1}):(({b}\'d1<<{b-1})-1); end')
        checks.append(f'if(g{i} !== n{i}) $fatal(1,"DSP mismatch {n} k=%0d a=%h b=%h gold=%h gate=%h",k,a{i},b{i},g{i},n{i});')
    tb='`timescale 1ns/1ps\nmodule tb; GSR GSR(.GSRI(1\'b1));\n'+'\n'.join(declarations)+"\ninteger k; reg [31:0] seed=32'h12345678; initial begin for(k=0;k<4096;k=k+1) begin\n"+'\n'.join(updates)+'\n#10;\n'+'\n'.join(checks)+'\nend $display("PASS DSP geometries and signed/unsigned corners"); $finish; end endmodule\n'
    (out/'tb.v').write_text(tb)
    env=dict(os.environ);env['PATH']=str(SUITE/'lib')+os.pathsep+env.get('PATH','')
    commands=[[str(SUITE/'bin/iverilog.exe'),'-B',str(SUITE/'lib/ivl'),'-g2012','-s','tb','-o',(out/'sim').as_posix(),(out/'design.v').as_posix(),(out/'tb.v').as_posix(),LIBRARY.as_posix()],
        [str(SUITE/'bin/vvp.exe'),'-M-','-M'+str(SUITE/'lib/ivl'),(out/'sim').as_posix()]]
    for i,command in enumerate(commands):
        cp=subprocess.run(command,env=env,capture_output=True,text=True,timeout=60)
        (out/f'step_{i}.log').write_text(cp.stdout+cp.stderr)
        if cp.returncode:raise RuntimeError('DSP validation failed: '+str(out/f'step_{i}.log'))
    if 'PASS DSP' not in cp.stdout:raise RuntimeError('DSP simulation did not complete')
    record.update(status='passed',vectors_per_geometry=4096,geometry_count=len(rows),model_sha256=EXPECTED)
    (out/'manifest.json').write_text(json.dumps(record,indent=2)+'\n');return record


if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('source',type=Path);ap.add_argument('--out',type=Path,required=True);args=ap.parse_args()
    print(json.dumps(verify(args.source.resolve(),args.out.resolve()),indent=2))
