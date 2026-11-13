"""Cold RTL-to-bitstream validation of the opt-in OREG packing path."""
import json
import argparse
from pathlib import Path
import subprocess
import sys
import xml.etree.ElementTree as ET
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'tests'))
ap=argparse.ArgumentParser();ap.add_argument('--out',type=Path,default=ROOT/'results/dsp_output_pack_smoke_002');args=ap.parse_args()
p=args.out.resolve();p.mkdir(parents=True,exist_ok=False)
(p/'top.v').write_text('module top(input clk,ce,input signed [17:0] a,input signed [12:0] b,output reg [5:0] q);wire signed [30:0] product=a*b;always @(posedge clk) if(ce) q<=product[5:0];endmodule\n')
(p/'clock.sdc').write_text('create_clock -name clk -period 10 [get_ports {clk}]\nset_input_delay -max 0 -clock clk [get_ports {a[*] b[*] ce}]\nset_input_delay -min 0 -clock clk [get_ports {a[*] b[*] ce}]\nset_output_delay -max 0 -clock clk [get_ports {q[*]}]\nset_output_delay -min 0 -clock clk [get_ports {q[*]}]\n')
prj=ET.Element('Project');ET.SubElement(prj,'Device',dict(partNumber='GW5AST-LV138FPG676AC1/I0'))
fl=ET.SubElement(prj,'FileList');ET.SubElement(fl,'File',dict(path=str(p/'top.v'),type='verilog'))
ol=ET.SubElement(prj,'OptionList');ET.SubElement(ol,'Option',dict(type='top_module',value='top'))
ET.ElementTree(prj).write(p/'frontend.prj',encoding='utf-8',xml_declaration=True)
(p/'profile.json').write_text(json.dumps(dict(part='GW5AST-LV138FPG676AC1/I0',clock_periods_ns={'clk':10.0})))
config=dict(project=str(p/'frontend.prj'),sdc=str(p/'clock.sdc'),physical_profile=str(p/'profile.json'),
    period_ps=10000,strategy='arch_delay',architecture_recipe='wide',resource_optimizations=True,
    pack_dsp_registers=True,soft_comparisons=True,register_alternatives=True,no_iob_registers=True,total_timeout=180)
(p/'config.json').write_text(json.dumps(config,indent=2)+'\n')
with (p/'flow.log').open('w') as log:
    cp=subprocess.run([sys.executable,str(ROOT/'scripts/nextgowin.py'),'--config',str(p/'config.json'),'--out',str(p/'flow')],stdout=log,stderr=subprocess.STDOUT,timeout=190)
print('Return code',cp.returncode)
if cp.returncode:print((p/'flow.log').read_text()[-3000:]);raise SystemExit(cp.returncode)
record=json.loads((p/'flow/03_resources/dsp_output_packing.json').read_text())
print('Packed DSPs',len(record['admitted_cells']),'removed FFs',record['removed_flipflops'])
print(json.loads((p/'flow/06_pnr/physical_summary.json').read_text())['violated_endpoints'])
