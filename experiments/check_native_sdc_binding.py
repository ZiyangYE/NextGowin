"""Controlled native RTL synthesis probe with a distinctive 37 ns clock."""
import argparse
import html
import json
import os
from pathlib import Path
import re
import subprocess
import sys
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from gowin_probe import DEVICE,PART

ap=argparse.ArgumentParser();ap.add_argument('--out',type=Path,required=True)
ap.add_argument('--before-extract',action='store_true');args=ap.parse_args()
p=args.out.resolve();p.mkdir(parents=True,exist_ok=False)
(p/'rtl.v').write_text('module top(input clk,a,b,rst,output reg q=0);always @(posedge clk) if(rst) q<=0;else q<=a^b;endmodule\n')
(p/'clock.sdc').write_text('create_clock -name DISTINCT_CLOCK -period 37 [get_ports {clk}]\nset_false_path -from [get_ports {rst}]\n')
dev=f'dev -p {DEVICE} -pn {PART}'
extract=f'extract -i {{{p.as_posix()}/rtl.v}} -e top -o {{{p.as_posix()}/elaborated.v}}'
sdc=f'sdc -i {{{p.as_posix()}/clock.sdc}}'
commands=[dev,*([sdc,extract] if args.before_extract else [extract,sdc]),'cvt','dio -novgopt','infer -ram -dsp','ato','dio','map -t','dio -divfanout',f'report -o {{{p.as_posix()}/report}} -r -l -t 5']
(p/'synth.tcl').write_text('\n'.join(commands)+'\n')
binary=ROOT/'binary_audit/flow/runtime/bin/python.exe'
with (p/'runner.log').open('w') as log:
    cp=subprocess.run([str(binary),str(ROOT/'scripts/gowin_probe.py'),'--worker','synth','--out',str(p)],
        cwd=binary.parent,env=dict(os.environ,PYTHONHOME=sys.base_prefix),stdout=log,stderr=subprocess.STDOUT,timeout=60)
text=(p/'report.html').read_text()
clean=lambda s:' '.join(html.unescape(re.sub(r'<[^>]*>',' ',s)).split())
ts=[[[clean(c) for c in re.findall(r'<t[dh]\b[^>]*>(.*?)</t[dh]>',r,re.S)] for r in re.findall(r'<tr\b[^>]*>(.*?)</tr>',t,re.S)] for t in re.findall(r'<table\b[^>]*>(.*?)</table>',text,re.S)]
clock=next((t for t in ts if t and t[0][:4]==['NO.','Clock Name','Type','Period']),None)
r=dict(returncode=cp.returncode,sdc_before_extract=args.before_extract,clock_table=clock,
       distinctive_period_honored=bool(clock and any(len(row)>3 and row[3]=='37.000' for row in clock[1:])),
       scope='Artificial RTL diagnostic. Native output is not used as an own synthesis seed.')
(p/'manifest.json').write_text(json.dumps(r,indent=2)+'\n');print(r)
