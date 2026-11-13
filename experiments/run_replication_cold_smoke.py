"""Cold RTL validation of public register/cluster flags and control cost."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import xml.etree.ElementTree as ET
ROOT=Path(__file__).resolve().parents[1]
ap=argparse.ArgumentParser();ap.add_argument('--out',type=Path,required=True);args=ap.parse_args()
out=args.out.resolve();out.mkdir(parents=True,exist_ok=False)
(out/'top.v').write_text('''module top(input clk,ce,input [15:0] a,output [15:0] q,output extra_q);
reg control=0,extra=0;reg [15:0] pipeline[0:14];integer i;
initial for(i=0;i<15;i=i+1) pipeline[i]=0;
always @(posedge clk) begin
 control<=ce;
 if(control) begin pipeline[0]<=a;for(i=1;i<15;i=i+1) pipeline[i]<=pipeline[i-1]^a;end
end
always @(posedge clk) if(control) extra<=0;else extra<=a[0];
assign q=pipeline[14];assign extra_q=extra;endmodule
''')
(out/'clock.sdc').write_text('create_clock -name clk -period 10 [get_ports {clk}]\n')
project=ET.Element('Project');ET.SubElement(project,'Device',dict(partNumber='GW5AST-LV138FPG676AC1/I0'))
fl=ET.SubElement(project,'FileList');ET.SubElement(fl,'File',dict(path=str(out/'top.v'),type='verilog'))
opts=ET.SubElement(project,'OptionList');ET.SubElement(opts,'Option',dict(type='top_module',value='top'))
ET.ElementTree(project).write(out/'frontend.prj',encoding='utf-8',xml_declaration=True)
(out/'profile.json').write_text(json.dumps(dict(part='GW5AST-LV138FPG676AC1/I0',clock_periods_ns={'clk':10.0})))
config=dict(project=str(out/'frontend.prj'),sdc=str(out/'clock.sdc'),physical_profile=str(out/'profile.json'),
    period_ps=10000,strategy='arch_delay',resource_optimizations=True,register_alternatives=True,
    register_replication=True,dedicated_cluster_replication=True,register_control_penalty_ps=206,
    no_iob_registers=True,total_timeout=240)
(out/'config.json').write_text(json.dumps(config,indent=2)+'\n')
with (out/'flow.log').open('w') as log:
    cp=subprocess.run([sys.executable,str(ROOT/'scripts/nextgowin.py'),'--config',str(out/'config.json'),
        '--out',str(out/'flow')],stdout=log,stderr=subprocess.STDOUT,timeout=250)
if cp.returncode:
    print((out/'flow.log').read_text()[-2500:]);raise SystemExit(cp.returncode)
r=json.loads((out/'flow/05_netlist/native_replication.json').read_text())
if r['registers']['clones']<1:raise ValueError('Cold RTL smoke did not exercise register replication')
physical=json.loads((out/'flow/06_pnr/physical_summary.json').read_text())
if not (out/'flow/06_pnr/mapped.fs').is_file():raise ValueError('Missing real bitstream')
print('Cold copies',r['registers']['clones'],'timing',physical['violated_endpoints'])
