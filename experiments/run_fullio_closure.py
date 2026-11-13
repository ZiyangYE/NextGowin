"""Check actual operating points; reported Fmax alone is not acceptance."""
import json,subprocess,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from project_timing import audit
from run_routing_ablation import pins
from flow_budget import Budget,activate

def main():
    import argparse
    ap=argparse.ArgumentParser();ap.add_argument('--extended',action='store_true');ap.add_argument('--edge',action='store_true');a=ap.parse_args()
    out=ROOT/'results'/('fullio_closure_003' if a.edge else 'fullio_closure_002' if a.extended else 'fullio_closure_001');out.mkdir(exist_ok=False);budget=Budget(1500);activate(budget);runs=[]
    cases=[];intbase=ROOT/'results/routing_ablation_002/int2float'
    for freq in (150,140):
        for name,vg in [('vendor',ROOT/'results/general_synthesis_suite_012/int2float/vendor_synthesis/mapped.vg'),('lut4',ROOT/'results/compact_output_ablation_001/int2float/05_netlist/mapped.vg')]:
            cases.append((f'int_{name}_local{freq}',freq,vg,intbase/'clock.sdc',ROOT/'results/int2float_clock_ablation_001'/f'{name}.cst'))
    cases.append(('int_rom100',100,ROOT/'results/int2float_rom_output_001/mapped.vg',intbase/'clock.sdc',intbase/'fixed.cst'))
    for name in ('uart','tmds'):
        base=ROOT/'results/tangprimer138_suite_001'/name
        for side,vg in [('vendor',base/'vendor_synthesis/mapped.vg'),('own',base/'own/lut4/05_netlist/mapped.vg' if name=='uart' else ROOT/'results/tmds_short_carry_001/lut4/05_netlist/mapped.vg')]:
            cases.append((name+'_'+side+'130',130,vg,base/'clock100.sdc',base/'fixed.cst'))
    if a.extended or a.edge:
        cases=[]
        for name,freq,vg in [('vendor',145,ROOT/'results/general_synthesis_suite_012/int2float/vendor_synthesis/mapped.vg'),('lut4',155,ROOT/'results/compact_output_ablation_001/int2float/05_netlist/mapped.vg')]:
            cases.append((f'int_{name}_local{freq}',freq,vg,intbase/'clock.sdc',ROOT/'results/int2float_clock_ablation_001'/f'{name}.cst'))
        for name in ('uart','tmds'):
            base=ROOT/'results/tangprimer138_suite_001'/name
            for side,vg in [('vendor',base/'vendor_synthesis/mapped.vg'),('own',base/'own/lut4/05_netlist/mapped.vg' if name=='uart' else ROOT/'results/tmds_short_carry_001/lut4/05_netlist/mapped.vg')]:
                freq=140 if name=='uart' and side=='own' else 135
                cases.append((name+'_'+side+str(freq),freq,vg,base/'clock100.sdc',base/'fixed.cst'))
        base=ROOT/'results/tangprimer138_cpu_002/vexriscv'
        for name,freq,vg in [('vendor',80,base/'vendor_synthesis/mapped.vg'),('wide',75,ROOT/'results/tangprimer138_cpu_attributes_003/wide/05_netlist/mapped.vg'),('lut4',75,ROOT/'results/tangprimer138_cpu_attributes_003/lut4/05_netlist/mapped.vg')]:
            cases.append(('cpu_'+name+str(freq),freq,vg,base/'clock100.sdc',base/'fixed.cst'))
        base=ROOT/'results/tangprimer138_lcd_002/lcd'
        for name,vg in [('vendor',base/'vendor_synthesis/mapped.vg'),('wide',base/'own/wide/05_netlist/mapped.vg')]:cases.append(('lcd_'+name+'60_core',60,vg,base/'clock100.sdc',base/'fixed.cst'))
    if a.edge:
        targets={'int_vendor_local145':149,'int_lut4_local155':157,'uart_vendor135':136,'uart_own140':143,'tmds_vendor135':136,'tmds_own135':136,'cpu_vendor80':82,'lcd_vendor60_core':78,'lcd_wide60_core':78}
        cases=[(name+'_edge',targets[name],vg,sdc,cst) for name,_,vg,sdc,cst in cases if name in targets]
    for name,freq,vg,source_sdc,cst in cases:
        row=dict(name=name,target_mhz=freq,status='running');runs.append(row)
        try:
            sdc=out/(name+'.sdc');text=source_sdc.read_text()
            if text.count('-period 10 ')!=1 or 'set_false_path' in text:raise ValueError('Expected full-I/O 100 MHz source constraints')
            sdc.write_text(text.replace('-period 10 ',f'-period {1000/freq:.9f} '));f=out/name
            with (out/(name+'.log')).open('w') as log:subprocess.run([sys.executable,str(ROOT/'scripts/run_gowin_netlist.py'),str(vg),'--sdc',str(sdc),'--cst',str(cst),'--out',str(f),'--no-iob-registers' if name.startswith('lcd_') else '--output-iob-registers','--timeout',str(int(budget.timeout(240,reserve=5)))],stdout=log,stderr=subprocess.STDOUT,check=True,timeout=budget.remaining())
            p=audit(f,dict(part='GW5AST-LV138FPG676AC1/I0',clock_periods_ns={'clk':1000/freq}));(f/'physical_audit.json').write_text(json.dumps(p,indent=2)+'\n')
            ref=ROOT/'results/general_synthesis_suite_012/int2float/vendor_pnr/mapped.pin.html' if name.startswith('int_') else ROOT/'results/tangprimer138_suite_001'/name.split('_')[0]/'vendor_pnr100/mapped.pin.html'
            if name.startswith('cpu_'):ref=ROOT/'results/tangprimer138_cpu_002/vexriscv/vendor_pnr100/mapped.pin.html'
            elif name.startswith('lcd_'):ref=ROOT/'results/tangprimer138_lcd_002/lcd/vendor_pnr100/mapped.pin.html'
            if pins(f/'mapped.pin.html')!=pins(ref):raise ValueError('Actual I/O mismatch')
            row.update(status='complete',frequency=p['frequency_reports'],closed=p['timing_closed'],violations=p['violated_endpoints'],resources=p['implementation']);print(name,row['frequency'],row['violations'],flush=True)
        except Exception as e:row.update(status='failed',error=repr(e));print(name,'FAILED',repr(e),flush=True)
        (out/'manifest.json').write_text(json.dumps(dict(status='running',runs=runs),indent=2)+'\n')
    (out/'manifest.json').write_text(json.dumps(dict(status='complete',scope='Actual complete I/O closure checks, no data path exceptions.',runs=runs,wall_seconds=budget.used()),indent=2)+'\n')
if __name__=='__main__':main()
