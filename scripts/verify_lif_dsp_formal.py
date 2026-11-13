"""Prove all LIF operand values against the pinned native bypass DSP model."""
import argparse
import json
import os
from pathlib import Path
import subprocess
from gw5a_dsp import prepare
from gowin_netlist_format import LIBRARY,EXPECTED
from run_abc_reproduction import ROOT,sha
from split_primitive_models import units
from yosys_runtime import run,quote,SUITE


def exhaustive(out,record,gold,gate,cases):
    decl=[];updates=[];checks=[];rows=[]
    for i,(name,coefficient) in enumerate(cases):
        r=next(r for r in record['multipliers'] if r['module']==name);a=r['a_width'];b=r['b_width'];w=r['output_width']
        literal=str(b)+"'b"+format(coefficient&((1<<b)-1),'0'+str(b)+'b')
        decl += [f'reg [{a-1}:0] a{i}=0;wire [{w-1}:0] g{i},n{i};',
            f'gold_{name} g_{i}(a{i},{literal},g{i});gate_{name} n_{i}(a{i},{literal},n{i});']
        updates.append(f'if(k<{1<<a}) a{i}=k;')
        checks.append(f'if(k<{1<<a} && g{i}!==n{i}) $fatal(1,"DSP mismatch {name} operand=%h",a{i});')
        rows.append(dict(module=name,coefficient=coefficient,operand_width=a,all_operand_values_checked=1<<a))
    tb='`timescale 1ns/1ps\nmodule tb;GSR GSR(.GSRI(1\'b1));\n'+'\n'.join(decl)+f'\ninteger k;initial begin for(k=0;k<{max(1<<r["operand_width"] for r in rows)};k=k+1) begin\n'+'\n'.join(updates)+'\n#1;\n'+'\n'.join(checks)+'\nend $display("PASS all LIF constant multiplier operands");$finish;end endmodule\n'
    (out/'design.v').write_text(gold+'\n'+gate);(out/'tb.v').write_text(tb)
    env=dict(os.environ);env['PATH']=str(SUITE/'lib')+os.pathsep+env.get('PATH','')
    commands=[[str(SUITE/'bin/iverilog.exe'),'-B',str(SUITE/'lib/ivl'),'-g2012','-s','tb','-o',(out/'sim').as_posix(),(out/'design.v').as_posix(),(out/'tb.v').as_posix(),LIBRARY.as_posix()],
        [str(SUITE/'bin/vvp.exe'),'-M-','-M'+str(SUITE/'lib/ivl'),(out/'sim').as_posix()]]
    for i,command in enumerate(commands):
        cp=subprocess.run(command,env=env,capture_output=True,text=True,timeout=120)
        (out/f'step_{i}.log').write_text(cp.stdout+cp.stderr)
        if cp.returncode:raise RuntimeError('Exhaustive verification failed: '+str(out/f'step_{i}.log'))
    if 'PASS all LIF' not in cp.stdout:raise RuntimeError('Exhaustive verification incomplete')
    result=dict(status='passed',method='exhaustive_finite_two_state',model_sha256=EXPECTED,cases=rows,
        unique_operand_cases=sum(r['all_operand_values_checked'] for r in rows),
        scope='Every operand bit pattern for the three actual fixed LIF coefficients, against original Verific helpers and the pinned actual native DSP model. No sequential retiming.')
    (out/'manifest.json').write_text(json.dumps(result,indent=2)+'\n');return result


def main():
    ap=argparse.ArgumentParser();ap.add_argument('source',type=Path);ap.add_argument('--out',type=Path,required=True)
    ap.add_argument('--sat',action='store_true',help='Use bounded SAT attempts instead of exhaustive finite enumeration');args=ap.parse_args()
    out=args.out.resolve();out.mkdir(parents=True,exist_ok=False)
    _,record,gold,gate=prepare(args.source.read_text())
    if sha(LIBRARY)!=EXPECTED:raise ValueError('Native model changed')
    cases=[('mult_19s_12s',1307),('mult_18s_13s',2588),('mult_18s_14s',-5191)]
    if not args.sat:
        print(json.dumps(exhaustive(out,record,gold,gate,cases),indent=2));return
    library=LIBRARY.read_text(encoding='utf-8');index=units(library)
    models='\n'.join(library[index[n]['start']:index[n]['end']] for n in ['MULT12X12','MULTALU27X18','MULT27X36'])
    results=[]
    for name,coefficient in cases:
        r=next(r for r in record['multipliers'] if r['module']==name);a=r['a_width'];b=r['b_width'];w=r['output_width']
        literal=str(b)+"'b"+format(coefficient&((1<<b)-1),'0'+str(b)+'b')
        miter=f'module compare(input [{a-1}:0] a,output same);wire [{w-1}:0] g,n; gold_{name} x(a,{literal},g);gate_{name} y(a,{literal},n);assign same=(g==n);endmodule\n'
        folder=out/name;folder.mkdir();(folder/'proof.v').write_text(models+'\n'+gold+'\n'+gate+'\n'+miter)
        invocation=run(['read_verilog -sv '+quote(folder/'proof.v'),'prep -top compare -flatten','opt',
            'sat -verify -prove same 1 -show-inputs -timeout 60'],folder,'proof',timeout=90)
        if 'SUCCESS!' not in (folder/'proof.log').read_text():raise ValueError('SAT proof success marker absent')
        results.append(dict(module=name,coefficient=coefficient,operand_width=a,all_operand_values_proven=True,invocation=invocation))
        (out/'manifest.json').write_text(json.dumps(dict(status='running',model_sha256=EXPECTED,cases=results),indent=2)+'\n')
    result=dict(status='passed',scope='All bit patterns of each LIF operand, for the three actual fixed coefficients; exact native combinational DSP model. No sequential retiming.',model_sha256=EXPECTED,cases=results)
    (out/'manifest.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result,indent=2))


if __name__=='__main__':main()
