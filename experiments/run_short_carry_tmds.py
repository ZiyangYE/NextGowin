import json,subprocess,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from lower_short_carry import lower
from native_boundary_cec import prove
from map_resource_json import map_design
from nextgowin import materialize
from project_timing import audit
from flow_budget import Budget,activate
from run_routing_ablation import pins

def main():
    out=ROOT/'results/tmds_short_carry_001';out.mkdir(exist_ok=False);budget=Budget(900);activate(budget)
    base=ROOT/'results/tangprimer138_suite_001/tmds';src=base/'own/shared';d=json.loads((src/'03_resources/resources_before_choices.json').read_text())
    after,record=lower(d);record['cec']=prove(d,after,out/'lowering_cec');(out/'lowering.json').write_text(json.dumps(record,indent=2)+'\n');resource=out/'03_resources';resource.mkdir();(resource/'resources.json').write_text(json.dumps(after)+'\n')
    results=[]
    for recipe in ['wide','lut4']:
        f=out/recipe;f.mkdir();map_design(resource/'resources.json',f/'04_mapping',5000,'arch_delay',recipe)
        materialize(f/'04_mapping/mapped.json',f/'05_netlist','svo_tmds',src/'02_bridge/vendor_cells.v',optimize_resources=False)
        for frequency in (100,200):
            folder=f/f'pnr{frequency}'
            try:
                with (f/f'pnr{frequency}.log').open('w') as log:subprocess.run([sys.executable,str(ROOT/'scripts/run_gowin_netlist.py'),str(f/'05_netlist/mapped.vg'),'--sdc',str(base/f'clock{frequency}.sdc'),'--cst',str(base/'fixed.cst'),'--out',str(folder),'--output-iob-registers','--timeout',str(int(budget.timeout(180,reserve=5)))],stdout=log,stderr=subprocess.STDOUT,check=True,timeout=budget.remaining())
                p=audit(folder,json.loads((base/f'profile{frequency}.json').read_text()));(folder/'physical_audit.json').write_text(json.dumps(p,indent=2)+'\n')
                if pins(folder/'mapped.pin.html')!=pins(base/'vendor_pnr100/mapped.pin.html'):raise ValueError('Actual I/O mismatch')
                r=dict(recipe=recipe,target=frequency,status='complete',frequency=p['frequency_reports'],closed=p['timing_closed'],violations=p['violated_endpoints'],resources=p['implementation']);print(r,flush=True)
            except Exception as e:r=dict(recipe=recipe,target=frequency,status='failed',error=repr(e));print(r,flush=True)
            results.append(r);(out/'manifest.json').write_text(json.dumps(dict(status='running',runs=results),indent=2)+'\n')
    (out/'manifest.json').write_text(json.dumps(dict(status='complete',runs=results,wall_seconds=budget.used(),scope='Short ALU chain lowering and no FF control rewrite; resource checkpoint candidate, not cold-start runtime.'),indent=2)+'\n')
if __name__=='__main__':main()
