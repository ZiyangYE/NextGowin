"""Sequential simulation of the CPU/GAO example's user logic and initial state.

The native GAO kernel is stubbed only for this user-datapath comparison; native
P&R is verified separately. This is a deterministic regression, not a general
sequential equivalence proof or an on-board GAO capture test.
"""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
from run_abc_reproduction import ROOT,sha
from yosys_runtime import SUITE
from gowin_netlist_format import LIBRARY,EXPECTED


def verify(flow,out):
    out.mkdir(parents=True,exist_ok=False)
    final=flow/'05_netlist/mapped.vg';macro=flow/'02_bridge/macro.vg'
    text=final.read_text(encoding='utf-8');payload=macro.read_text(encoding='utf-8')
    if not text.endswith(payload):raise ValueError('Final linked macro suffix changed')
    core=text[:-len(payload)]
    rtl=ROOT/'examples/cpu_gao/top.v'
    golden=re.sub(r'\bmodule\s+top\b','module gold',rtl.read_text(),count=1)
    d=json.loads((flow/'05_netlist/final.json').read_text())
    ports=d['modules']['top']['ports'];connections=[]
    for name in ports:
        signal=name
        if name=='q':signal='mapped_q'
        if name in ('tms_pad_i','tck_pad_i','tdi_pad_i'):signal="1'b0"
        if name=='tdo_pad_o':signal='tdo'
        formal='\\'+name+' ' if '[' in name else name
        connections.append('.'+formal+'('+signal+')')
    bench='''`timescale 1ns/1ps
module gw_gao(input a,q,clk,tms_pad_i,tck_pad_i,tdi_pad_i,output tdo_pad_o);
assign tdo_pad_o=1'b0;
endmodule
module tb;
reg clk=0,a=0,ce=0,rst=0; reg [7:0] b=0; wire mapped_q,golden_q,tdo;
GSR GSR(.GSRI(1'b1));
gold reference(.clk(clk),.a(a),.b(b),.ce(ce),.rst(rst),.q(golden_q));
top mapped('''+', '.join(connections)+''');
integer i; reg [31:0] seed=32'h8b173251;
initial begin
 #1; if(mapped_q!==1'b1 || mapped_q!==golden_q) $fatal(1,"initial q differs");
 for(i=0;i<2000;i=i+1) begin
  clk=0; seed=seed*32'd1664525+32'd1013904223;
  a=seed[8]; b=seed[7:0]; ce=seed[9]; rst=(i%31==0);
  #5; clk=1; #1;
  if(mapped_q!==golden_q) $fatal(1,"datapath mismatch cycle %0d",i);
 end
 $display("PASS initial state and 2000 user-datapath cycles"); $finish;
end
endmodule
'''
    (out/'core.v').write_text(core,encoding='utf-8')
    (out/'gold.v').write_text(golden)
    (out/'tb.v').write_text(bench)
    if sha(LIBRARY)!=EXPECTED:raise ValueError('Vendor simulation model changed')
    env=dict(os.environ);env['PATH']=str(SUITE/'lib')+os.pathsep+env.get('PATH','')
    iv=SUITE/'bin/iverilog.exe';vvp=SUITE/'bin/vvp.exe'
    commands=[
        [str(iv),'-B',str(SUITE/'lib/ivl'),'-g2012','-s','tb','-o',(out/'sim').as_posix(),
         (out/'gold.v').as_posix(),(out/'core.v').as_posix(),LIBRARY.as_posix(),(out/'tb.v').as_posix()],
        [str(vvp),'-M-','-M'+str(SUITE/'lib/ivl'),(out/'sim').as_posix()]]
    record=dict(scope=__doc__,flow_netlist_sha256=sha(final),rtl_sha256=sha(rtl),
                vendor_simulation_library_sha256=EXPECTED,commands=commands,status='running')
    for index,command in enumerate(commands):
        cp=subprocess.run(command,cwd=ROOT,env=env,capture_output=True,text=True,timeout=60)
        (out/f'step_{index}.log').write_text(cp.stdout+'\n'+cp.stderr)
        if cp.returncode:
            record.update(status='failed',failed_step=index,returncode=cp.returncode)
            (out/'manifest.json').write_text(json.dumps(record,indent=2)+'\n')
            raise RuntimeError('Datapath simulation failed: '+str(out/f'step_{index}.log'))
    if 'PASS initial state and 2000' not in cp.stdout:raise ValueError('Simulation did not finish assertions')
    record.update(status='passed',cycles=2000,initial_state_checked=True,gao_stubbed=True,board_programmed=False)
    (out/'manifest.json').write_text(json.dumps(record,indent=2)+'\n')
    return record


def main():
    ap=argparse.ArgumentParser();ap.add_argument('flow',type=Path);ap.add_argument('--out',type=Path,required=True)
    args=ap.parse_args();print(json.dumps(verify(args.flow.resolve(),args.out.resolve()),indent=2))


if __name__=='__main__':main()
