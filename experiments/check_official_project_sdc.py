"""Verify the same distinctive clock through the official project CLI."""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
ROOT=Path(__file__).resolve().parents[1]

ap=argparse.ArgumentParser();ap.add_argument('--out',type=Path,required=True);args=ap.parse_args()
p=args.out.resolve();p.mkdir(parents=True,exist_ok=False)
(p/'rtl.v').write_text('module top(input clk,a,b,rst,output reg q=0);always @(posedge clk) if(rst) q<=0;else q<=a^b;endmodule\n')
(p/'clock.sdc').write_text('create_clock -name DISTINCT_CLOCK -period 37 [get_ports {clk}]\nset_false_path -from [get_ports {rst}]\n')
project=ET.Element('Project');ET.SubElement(project,'Template').text='FPGA';ET.SubElement(project,'Version').text='5'
ET.SubElement(project,'Device',dict(name='GW5AST-138C',pn='GW5AST-LV138FPG676AC1/I0')).text='gw5ast138c-003'
files=ET.SubElement(project,'FileList')
for name,kind in [('rtl.v','file.verilog'),('clock.sdc','file.sdc')]:
    ET.SubElement(files,'File',dict(path=(p/name).as_posix(),type=kind,enable='1'))
ET.ElementTree(project).write(p/'probe.gprj',encoding='utf-8',xml_declaration=True)
(p/'run.tcl').write_text('open_project {'+(p/'probe.gprj').as_posix()+'}\nset_option -top_module top\nset_option -synthesis_tool gowinsynthesis\nset_option -run_timing_driven 1\nrun syn\nexit\n')
start=time.perf_counter();record=dict(scope='Artificial RTL; official gw_sh project synthesis only, no programming or own-candidate seed.')
try:
    with (p/'runner.log').open('w') as log:
        cp=subprocess.run(['C:/Gowin/Gowin_V1.9.12.04_x64/IDE/bin/gw_sh.exe',str(p/'run.tcl')],
            cwd=p,stdout=log,stderr=subprocess.STDOUT,timeout=60,creationflags=subprocess.CREATE_NO_WINDOW)
    record.update(returncode=cp.returncode,status='complete' if cp.returncode==0 else 'failed')
except Exception as e:record.update(status='failed',error=repr(e))
record['wall_seconds']=time.perf_counter()-start
record['reports']=[str(f.relative_to(p)) for f in p.rglob('*.html')]
(p/'manifest.json').write_text(json.dumps(record,indent=2)+'\n');print(record)
