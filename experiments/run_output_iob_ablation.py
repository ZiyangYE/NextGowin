"""Separate target-period effects from input/output register placement."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from flow_budget import Budget,activate
from project_timing import audit
from routing_path_diagnosis import diagnose
from run_routing_ablation import pins
from run_abc_reproduction import sha

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--out',type=Path,required=True);a=ap.parse_args()
    out=a.out.resolve();out.mkdir(parents=True,exist_ok=False);budget=Budget(1500);activate(budget)
    report=dict(status='running',scope='Fixed identical pins, core vs output-only IOB; unchanged input/output delays and hold uncertainty.',runs=[])
    def save():(out/'manifest.json').write_text(json.dumps(report,indent=2)+'\n')
    save()
    for name in ['ctrl','int2float','router']:
        ref=ROOT/'results/routing_ablation_002'/name;folder=out/name;folder.mkdir()
        pinref=pins(ROOT/'results/general_synthesis_suite_012'/name/'vendor_pnr/mapped.pin.html')
        for variant,period,flag in [('core160',6.25,'--no-iob-registers'),('output100',10.,'--output-iob-registers'),('output160',6.25,'--output-iob-registers')]:
            for side in ['vendor','own']:
                vg=(ROOT/'results/general_synthesis_suite_012'/name/'vendor_synthesis/mapped.vg') if side=='vendor' else (ROOT/'results/fanout_development_suite_001'/name/'own/05_netlist/mapped.vg')
                target=folder/(side+'_'+variant);sdc=ref/('clock160.sdc' if period==6.25 else 'clock.sdc');cst=ref/'fixed.cst'
                row=dict(design=name,variant=side+'_'+variant,status='running',netlist_sha256=sha(vg),sdc_sha256=sha(sdc),cst_sha256=sha(cst));report['runs'].append(row);save()
                try:
                    cmd=[sys.executable,str(ROOT/'scripts/run_gowin_netlist.py'),str(vg),'--sdc',str(sdc),'--cst',str(cst),'--out',str(target),'--timeout',str(int(budget.timeout(180,reserve=5))),flag]
                    with (folder/(side+'_'+variant+'.log')).open('w') as log:subprocess.run(cmd,stdout=log,stderr=subprocess.STDOUT,check=True,timeout=budget.remaining())
                    phy=audit(target,dict(part='GW5AST-LV138FPG676AC1/I0',clock_periods_ns={'clk':period}))
                    if pins(target/'mapped.pin.html')!=pinref:raise ValueError('Actual pin/electrical mismatch')
                    paths=diagnose((target/'mapped.tr').read_text());(target/'routing_diagnosis.json').write_text(json.dumps(paths,indent=2)+'\n')
                    row.update(status='complete',fmax_mhz=phy['frequency_reports']['clk']['reported_fmax_mhz'],violations=phy['violated_endpoints'],timing_closed=phy['timing_closed'],resources=phy['implementation'],worst_path=paths[0],pins_verified=True)
                    print(name,side,variant,row['fmax_mhz'],row['violations'],'wire',round(paths[0]['data_wire_ns'],3),flush=True)
                except Exception as e:row.update(status='failed',error=repr(e));print(name,side,variant,'FAILED',repr(e),flush=True)
                save()
    report.update(status='complete' if all(r['status']=='complete' for r in report['runs']) else 'completed_with_failures',wall_seconds=budget.used());save()

if __name__=='__main__':main()
