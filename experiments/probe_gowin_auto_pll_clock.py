"""Check native PLL clock derivation without replacing it with a manual clock."""
import argparse,json,subprocess,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path[:0]=[str(ROOT/'scripts'),str(ROOT/'experiments')]
from project_timing import audit
from run_routing_ablation import pins
from summarize_practical138 import coverage
from routing_path_diagnosis import diagnose

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--out',type=Path,required=True);args=ap.parse_args()
    out=args.out.resolve();out.mkdir(exist_ok=False);rows=[]
    for name,base in [('internal','int2float_pll_ablation_002'),('external','int2float_pll_external_001')]:
        source=ROOT/'results'/base/'lut4/pnr';f=out/name;sdc=out/f'{name}.sdc'
        original=(source/'project.sdc').read_text();lines=[line for line in original.splitlines() if 'generated_clock' not in line and 'uncertainty' not in line]
        # Native default clocks are derived after SDC parsing; naming them in
        # uncertainty commands is rejected. Keep this a diagnostic, never an
        # accepted replacement for the fully constrained 50-ps hold contract.
        lines.append('set_clock_uncertainty -hold 0.050 -from [get_clocks {clk}] -to [get_clocks {clk}]')
        sdc.write_text('\n'.join(lines)+'\n')
        row=dict(name=name,status='running',accepted=False,reason='Generated-clock hold uncertainty is not overridden; diagnostic only.');rows.append(row)
        try:
            with (out/f'{name}.log').open('w') as log:subprocess.run([sys.executable,str(ROOT/'scripts/run_gowin_netlist.py'),str(source/'mapped.vg'),'--sdc',str(sdc),'--cst',str(source/'project.cst'),'--out',str(f),'--output-iob-registers','--timeout','120'],stdout=log,stderr=subprocess.STDOUT,check=True,timeout=140)
            p=audit(f,dict(part='GW5AST-LV138FPG676AC1/I0',clock_periods_ns={'clk':5.,'ng_pll/CLKOUT0.default_gen_clk':5.}))
            if pins(source/'mapped.pin.html')!=pins(f/'mapped.pin.html'):raise ValueError('Package pin/electrical assignment changed')
            (f/'physical_audit.json').write_text(json.dumps(p,indent=2)+'\n')
            row.update(status='complete',closed=p['timing_closed'],violations=p['violated_endpoints'],frequency=p['frequency_reports'],coverage=coverage(f),critical_paths=diagnose((f/'mapped.tr').read_text()))
        except Exception as e:row.update(status='failed',error=repr(e))
        print(name,row['status'],row.get('violations'),row.get('error'),flush=True)
        (out/'manifest.json').write_text(json.dumps(dict(status='complete' if all(r['status']=='complete' for r in rows) else 'completed_with_failures',scope='Automatic generated-clock diagnostic; full I/O, original pins. PLL startup equivalence not established.',rows=rows),indent=2)+'\n')
if __name__=='__main__':main()
