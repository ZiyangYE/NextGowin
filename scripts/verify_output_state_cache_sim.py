"""Exhaustive one-step cross-check using the installed Gowin primitive model."""
import argparse,json,os,subprocess,time,hashlib
from pathlib import Path
from ownpr_device import ROOT
from ownpr_image_storage import load_json,sha
from ownpr_output_state_cache import plan,verify
from ownpr_multilevel_cut import Cuts
from yosys_runtime import SUITE
from gowin_installation import SDK
from workspace_budget import reserve


def run(before,after,obuf,ff,out,allow_general=False):
    reserve(ROOT,20000000);out=Path(out).resolve();out.mkdir(exist_ok=False);stamp=time.monotonic();proof=verify(before,after,obuf,ff,allow_general);info=plan(before,obuf,allow_general)
    cells=after['modules']['top']['cells'];added=set(cells)-set(before['modules']['top']['cells']);model=Cuts(before);variables=info['variables'];indices={b:i for i,b in enumerate(variables)};qs={r['q']:'q'+str(i) for i,r in enumerate(info['states'])}
    fresh={b:'n'+str(b) for n in added for p,bs in cells[n]['connections'].items() if cells[n]['port_directions'][p]=='output' for b in bs}
    def expr(bit):
        if bit in model.constants:return "1'b"+model.constants[bit]
        if bit in qs:return qs[bit]
        if bit in fresh:return fresh[bit]
        return 'v['+str(indices[bit])+']'
    lines=['`timescale 1ns/1ps','module tb;',f'reg [{len(variables)-1}:0] v=0; reg clk=0; reg gsr=1;', 'GSR GSR(.GSRI(gsr));','wire '+','.join([*qs.values(),*fresh.values()])+';']
    for i,row in enumerate(info['states']):
        lines.append(f"DFFRE #(.INIT(1'b{row['initial']})) old_{i}(.Q(q{i}),.D({expr(row['d'])}),.CLK(clk),.CE({expr(row['ce'])}),.RESET({expr(row['reset'])}));")
    for n in sorted(added):
        c=cells[n];ports=','.join('.'+p+'('+('clk' if p=='CLK' else expr(bs[0]))+')' for p,bs in c['connections'].items())
        init=c['parameters']['INIT'];lines.append(f"{c['type']} #(.INIT({len(init)}'b{init})) {n}({ports});")
    width=1<<len(info['states']);index='{'+','.join(qs[r['q']] for r in reversed(info['states']))+'}'
    lines.extend([f"localparam [{width-1}:0] TRUTH={width}'h{int(info['truth_hex'],16):x};",f'wire expected=TRUTH[{index}];','integer i; integer gsr_seen=0;','initial begin',f'#1; if ({expr(cells[ff]["connections"]["Q"][0])} !== expected) $fatal(1,"initial mismatch");',f'for(i=0;i<{1<<len(variables)};i=i+1) begin','clk=0; v=i; #1;'])
    for i,row in enumerate(info['states']):lines.append(f"if(v[{indices[row['q']]}]) force old_{i}.Q_reg=1'b1; else force old_{i}.Q_reg=1'b0;")
    lines.append('#1;')
    for i in range(len(info['states'])):lines.append(f'release old_{i}.Q_reg;')
    actual=expr(cells[ff]['connections']['Q'][0]);seed='{'+','.join('v['+str(indices[r['q']])+']' for r in reversed(info['states']))+'}'
    lines.extend(['#1;',f'if ({index} !== {seed}) $fatal(1,"state preload mismatch %0d",i);','clk=1; #1;',
        f'if ({actual} !== expected) $fatal(1,"transition mismatch %0d",i);',
        "if (expected === 1'b1 && !gsr_seen) begin gsr=0; #1;",
        f'if ({actual} !== 1\'b0 || expected !== 1\'b0) $fatal(1,"active GSR mismatch");',
        'gsr=1; #1;',f'if ({actual} !== expected) $fatal(1,"active GSR release mismatch");','gsr_seen=1; end','end',
        'clk=0; gsr=0; #1;',f'if ({actual} !== expected || {actual} !== 1\'b0) $fatal(1,"GSR mismatch");','gsr=1; #1;',f'if ({actual} !== expected) $fatal(1,"GSR release mismatch");',
        '$display("GSR_FROM_ONE %0d",gsr_seen);',f'$display("OUTPUT_STATE_CACHE_PASS {1<<len(variables)}"); $finish;','end','endmodule'])
    tb=out/'tb.v';tb.write_text('\n'.join(lines)+'\n');image=out/'simulation.vvp';library=SDK/'simlib/gw5a/prim_sim.v'
    env=dict(os.environ);env['PATH']=str(SUITE/'bin')+os.pathsep+str(SUITE/'lib')+os.pathsep+env.get('PATH','')
    if os.name=='nt':
        import ctypes
        ctypes.windll.kernel32.SetErrorMode(0x0001|0x8000)
    command=[str(SUITE/'bin/iverilog.exe'),'-B',str(SUITE/'lib/ivl'),'-g2012','-s','tb','-o',str(image),str(tb),str(library)]
    with (out/'compile.log').open('w') as log:subprocess.run(command,cwd=ROOT,stdout=log,stderr=subprocess.STDOUT,env=env,check=True,timeout=90)
    cp=subprocess.run(['vvp.exe','-M','-','-M',str(SUITE/'lib/ivl'),str(image)],executable=str(SUITE/'bin/vvp.exe'),cwd=SUITE/'bin',capture_output=True,text=True,env=env,timeout=90)
    (out/'simulation.log').write_text(cp.stdout+cp.stderr);cp.check_returncode()
    if f'OUTPUT_STATE_CACHE_PASS {1<<len(variables)}' not in cp.stdout:raise ValueError('Vendor simulation did not finish')
    digest=lambda value:hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    report=dict(status='vendor_model_exhaustive_step_passed',source_design_canonical_sha256=digest(before),candidate_design_canonical_sha256=digest(after),vectors=1<<len(variables),initial_and_gsr_checked=True,gsr_assertion_from_one_checked='GSR_FROM_ONE 1' in cp.stdout,source_state_preload_checked=True,primitive_model_sha256=sha(library),testbench_sha256=sha(tb),software_proof=proof,wall_seconds=time.monotonic()-stamp,full_io_sta_verified=False,hardware_verified=False,scope='Two-valued cycle model only; forced source states cover every abstract step; no analog/CDC or timing equivalence claim')
    report['general_function']=info['general_function']
    (out/'manifest.json').write_text(json.dumps(report,indent=2));return report


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--before',type=Path,required=True);p.add_argument('--after',type=Path,required=True);p.add_argument('--obuf',required=True);p.add_argument('--ff',required=True);p.add_argument('--out',type=Path,required=True);p.add_argument('--general-function',action='store_true');a=p.parse_args()
    print(json.dumps(run(load_json(a.before),load_json(a.after),a.obuf,a.ff,a.out,a.general_function),indent=2))
