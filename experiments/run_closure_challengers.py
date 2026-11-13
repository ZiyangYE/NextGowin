"""Test the other side at the higher verified target; avoid asymmetric claims."""
import json,subprocess,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from project_timing import audit
from summarize_practical138 import coverage
from run_routing_ablation import pins

out=ROOT/'results/fullio_challengers_001';out.mkdir(exist_ok=False);rows=[]
uart=ROOT/'results/tangprimer138_suite_001/uart';cpu=ROOT/'results/tangprimer138_cpu_002/vexriscv'
cases=[('uart_vendor143',143,uart/'vendor_synthesis/mapped.vg',uart),('cpu_wide82',82,ROOT/'results/tangprimer138_cpu_attributes_003/wide/05_netlist/mapped.vg',cpu),('cpu_lut482',82,ROOT/'results/tangprimer138_cpu_attributes_003/lut4/05_netlist/mapped.vg',cpu)]
for name,freq,vg,base in cases:
    row=dict(name=name,target=freq,status='running');rows.append(row);sdc=out/(name+'.sdc');sdc.write_text((base/'clock100.sdc').read_text().replace('-period 10 ',f'-period {1000/freq:.9f} '));f=out/name
    try:
        with (out/(name+'.log')).open('w') as log:subprocess.run([sys.executable,str(ROOT/'scripts/run_gowin_netlist.py'),str(vg),'--sdc',str(sdc),'--cst',str(base/'fixed.cst'),'--out',str(f),'--output-iob-registers','--timeout','240'],stdout=log,stderr=subprocess.STDOUT,check=True,timeout=250)
        p=audit(f,dict(part='GW5AST-LV138FPG676AC1/I0',clock_periods_ns={'clk':1000/freq}));(f/'physical_audit.json').write_text(json.dumps(p,indent=2)+'\n')
        if pins(f/'mapped.pin.html')!=pins(base/'vendor_pnr100/mapped.pin.html'):raise ValueError('Actual I/O mismatch')
        row.update(status='complete',frequency=p['frequency_reports'],closed=p['timing_closed'],violations=p['violated_endpoints'],coverage=coverage(f));print(row,flush=True)
    except Exception as e:row.update(status='failed',error=repr(e));print(row,flush=True)
    (out/'manifest.json').write_text(json.dumps(dict(status='running',runs=rows),indent=2)+'\n')
(out/'manifest.json').write_text(json.dumps(dict(status='complete',runs=rows),indent=2)+'\n')
