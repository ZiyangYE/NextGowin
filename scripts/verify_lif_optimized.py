"""Exercise the complete optimized LIF pipeline against its original RTL."""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from run_abc_reproduction import ROOT,sha
from yosys_runtime import SUITE
from gowin_netlist_format import LIBRARY,EXPECTED


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--out',type=Path,required=True)
    ap.add_argument('--soft-comparisons',action='store_true');args=ap.parse_args()
    out=args.out.resolve();out.mkdir(parents=True,exist_ok=False)
    source=ROOT/'FPGAFLY_ISCAS2027/rtl/state/lif_pipeline.v'
    project=ET.Element('Project');ET.SubElement(project,'Device',dict(partNumber='GW5AST-LV138FPG676AC1/I0'))
    files=ET.SubElement(project,'FileList');ET.SubElement(files,'File',dict(path=str(source),type='verilog'))
    options=ET.SubElement(project,'OptionList');ET.SubElement(options,'Option',dict(type='top_module',value='lif_pipeline'))
    ET.ElementTree(project).write(out/'frontend.prj',encoding='utf-8',xml_declaration=True)
    config=dict(project=str(out/'frontend.prj'),period_ps=12500,strategy='acd_remap',resource_optimizations=True,timeout=120)
    if args.soft_comparisons:config['soft_comparisons']=True
    (out/'config.json').write_text(json.dumps(config,indent=2)+'\n')
    with (out/'flow.log').open('w') as log:
        subprocess.run([sys.executable,str(ROOT/'scripts/nextgowin.py'),'--config',str(out/'config.json'),
            '--out',str(out/'flow'),'--stop-after','mapping'],stdout=log,stderr=subprocess.STDOUT,check=True,timeout=300)
    native=out/'flow/05_netlist/mapped.vg';d=json.loads((out/'flow/05_netlist/final.json').read_text())
    ports=d['modules']['lif_pipeline']['ports'];connections=[]
    for name,row in ports.items():
        match=re.fullmatch(r'(\w+)(\[\d+\])?',name)
        if not match:raise ValueError('Unexpected split port')
        base,index=match[1],match[2] or '';signal=('n_'+base if row['direction']=='output' else base)+index
        formal='\\'+name+' ' if '[' in name else name
        connections.append('.'+formal+'('+signal+')')
    (out/'gold.v').write_text(re.sub(r'\bmodule\s+lif_pipeline\b','module gold',source.read_text(),count=1))
    tb='''`timescale 1ns/1ps
module tb;
reg clk=0,rst=1,valid=0,out_ready=0;reg [17:0] tag=0;reg signed [17:0] u=0,g=0,drive=0;reg [4:0] refractory=0;
wire ready,out_valid,n_ready,n_out_valid;wire [17:0] out_tag,n_out_tag;
wire signed [17:0] next_u,next_g,n_next_u,n_next_g;wire [4:0] next_refractory,n_next_refractory;
wire fire,clipped,state_wide,n_fire,n_clipped,n_state_wide;
GSR GSR(.GSRI(1'b1));
gold reference(.*);
lif_pipeline mapped('''+', '.join(connections)+''');
integer i;reg [31:0] seed=32'h3829a517;
initial begin
 #1;if(ready!==n_ready || out_valid!==n_out_valid) $fatal(1,"initial control mismatch");
 for(i=0;i<4096;i=i+1) begin
 clk=0;seed=seed*32'd1664525+32'd1013904223;
 rst=(i<3 || i%173==0);valid=seed[4];out_ready=(i%41<12)?0:seed[5];
 tag=i;u=seed[17:0];seed=seed*32'd1664525+32'd1013904223;g=seed[17:0];
 seed=seed*32'd1664525+32'd1013904223;drive=seed[17:0];refractory=seed[24:20];
 if(i%32==0) begin u=-18'sd131072;g=18'sd131071;drive=-18'sd131072;refractory=0;end
 #5;clk=1;#2;
 if(ready!==n_ready || out_valid!==n_out_valid) $fatal(1,"control mismatch cycle %0d",i);
 if(out_valid && {out_tag,next_u,next_g,next_refractory,fire,clipped,state_wide} !==
 {n_out_tag,n_next_u,n_next_g,n_next_refractory,n_fire,n_clipped,n_state_wide}) $fatal(1,"LIF data mismatch cycle %0d",i);
 end $display("PASS optimized LIF: 4096 cycles, stalls/reset/signed corners");$finish;
end endmodule
'''
    (out/'tb.v').write_text(tb)
    if sha(LIBRARY)!=EXPECTED:raise ValueError('Simulation model changed')
    env=dict(os.environ);env['PATH']=str(SUITE/'lib')+os.pathsep+env.get('PATH','')
    commands=[[str(SUITE/'bin/iverilog.exe'),'-B',str(SUITE/'lib/ivl'),'-g2012','-s','tb','-o',(out/'sim').as_posix(),(out/'gold.v').as_posix(),native.as_posix(),(out/'tb.v').as_posix(),LIBRARY.as_posix()],
        [str(SUITE/'bin/vvp.exe'),'-M-','-M'+str(SUITE/'lib/ivl'),(out/'sim').as_posix()]]
    for i,command in enumerate(commands):
        cp=subprocess.run(command,env=env,capture_output=True,text=True,timeout=90)
        (out/f'step_{i}.log').write_text(cp.stdout+cp.stderr)
        if cp.returncode:raise RuntimeError('LIF regression failed: '+str(out/f'step_{i}.log'))
    if 'PASS optimized LIF' not in cp.stdout:raise RuntimeError('LIF regression did not finish')
    result=dict(status='passed',cycles=4096,scope='Native optimized LIF netlist against original RTL; valid-output data, backpressure, reset, signed corners. Not full-board equivalence.',
        source_sha256=sha(source),netlist_sha256=sha(native),model_sha256=EXPECTED)
    (out/'manifest.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result,indent=2))


if __name__=='__main__':main()
