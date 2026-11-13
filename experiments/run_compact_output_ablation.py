"""Test LUT4 mapping with output-only IOB on the same three declared designs."""
import json
from pathlib import Path
import shutil
import subprocess
import sys
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from flow_budget import Budget,activate
from map_resource_json import map_design
from nextgowin import materialize
from registered_benchmark import prove
from gowin_blif import read_blif
from project_timing import audit
from routing_path_diagnosis import diagnose
from run_routing_ablation import pins

def main():
    import argparse
    ap=argparse.ArgumentParser();ap.add_argument('--out',type=Path,required=True);a=ap.parse_args()
    out=a.out.resolve();out.mkdir(parents=True,exist_ok=False);budget=Budget(900);activate(budget)
    report=dict(status='running',scope='Only recipe wide -> LUT4; same replication/control settings and output-only IOB as reference. Resource checkpoint reuse, not RTL cold-start timing.',runs=[])
    def save():(out/'manifest.json').write_text(json.dumps(report,indent=2)+'\n')
    save()
    for name in ['ctrl','int2float','router']:
        f=out/name;f.mkdir();source=ROOT/'results/fanout_development_suite_001'/name;ref=ROOT/'results/routing_ablation_002'/name
        row=dict(design=name,status='running');report['runs'].append(row);save()
        try:
            resource=f/'03_resources';resource.mkdir()
            for file in ('register_choices.json','dsp_cells.v'):
                if (source/'own/03_resources'/file).exists():shutil.copy2(source/'own/03_resources'/file,resource/file)
            map_design(source/'own/03_resources/resources.json',f/'04_mapping',10000,'arch_delay','lut4')
            materialize(f/'04_mapping/mapped.json',f/'05_netlist','top',source/'own/02_bridge/vendor_cells.v',optimize_resources=True,register_alternatives=True,logic_replication=True,register_replication=True,register_control_penalty_ps=206)
            proof=f/'functional_proof';proof.mkdir();shutil.copy2(source/'reference.blif',proof/'reference.blif')
            prove(json.loads((f/'05_netlist/final.json').read_text()),read_blif(source/'reference.blif',max_inputs=11),proof)
            target=f/'06_pnr';cmd=[sys.executable,str(ROOT/'scripts/run_gowin_netlist.py'),str(f/'05_netlist/mapped.vg'),'--sdc',str(ref/'clock.sdc'),'--cst',str(ref/'fixed.cst'),'--out',str(target),'--output-iob-registers','--timeout',str(int(budget.timeout(180,reserve=5)))]
            with (f/'pnr_runner.log').open('w') as log:subprocess.run(cmd,stdout=log,stderr=subprocess.STDOUT,check=True,timeout=budget.remaining())
            phy=audit(target,dict(part='GW5AST-LV138FPG676AC1/I0',clock_periods_ns={'clk':10.}))
            if pins(target/'mapped.pin.html')!=pins(ROOT/'results/general_synthesis_suite_012'/name/'vendor_pnr/mapped.pin.html'):raise ValueError('Actual pin/electrical mismatch')
            paths=diagnose((target/'mapped.tr').read_text());(target/'routing_diagnosis.json').write_text(json.dumps(paths,indent=2)+'\n')
            row.update(status='complete',fmax_mhz=phy['frequency_reports']['clk']['reported_fmax_mhz'],violations=phy['violated_endpoints'],timing_closed=phy['timing_closed'],resources=phy['implementation'],worst_path=paths[0],pins_verified=True)
            print(name,row['fmax_mhz'],row['violations'],'wire',round(paths[0]['data_wire_ns'],3),flush=True)
        except Exception as e:row.update(status='failed',error=repr(e));print(name,'FAILED',repr(e),flush=True)
        save()
    report.update(status='complete' if all(r['status']=='complete' for r in report['runs']) else 'completed_with_failures',wall_seconds=budget.used());save()

if __name__=='__main__':main()
