import argparse,json,re,subprocess,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from project_timing import audit
from routing_path_diagnosis import diagnose
from run_routing_ablation import pins
from flow_budget import Budget,activate

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--out',type=Path,required=True);a=ap.parse_args();out=a.out.resolve();out.mkdir(exist_ok=False)
    budget=Budget(900);activate(budget);manifest=dict(status='running',scope='Full I/O at 200 MHz; only clock routing policy changes. Not an endorsement of general-routing clocks.',runs=[])
    def save():(out/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    ref=ROOT/'results/routing_ablation_002/int2float';sdc=ROOT/'results/int2float_fullio_rom_001/clock200.sdc'
    inputs={'vendor':ROOT/'results/general_synthesis_suite_012/int2float/vendor_synthesis/mapped.vg','wide':ROOT/'results/fanout_development_suite_001/int2float/own/05_netlist/mapped.vg','lut4':ROOT/'results/compact_output_ablation_001/int2float/05_netlist/mapped.vg','rom':ROOT/'results/int2float_registered_rom_native_001/mapped.vg'}
    expected=pins(ROOT/'results/general_synthesis_suite_012/int2float/vendor_pnr/mapped.pin.html')
    for name,vg in inputs.items():
        row=dict(name=name,status='running');manifest['runs'].append(row);save()
        try:
            nets=set(re.findall(r'\.CLK\(([^)]+)\)',vg.read_text()))
            if len(nets)!=1:raise ValueError('Expected exactly one clock net')
            net=nets.pop().strip().removeprefix('\\');cst=out/(name+'.cst');cst.write_text((ref/'fixed.cst').read_text()+f'CLOCK_LOC "{net}" LOCAL_CLOCK;\n')
            folder=out/name;cmd=[sys.executable,str(ROOT/'scripts/run_gowin_netlist.py'),str(vg),'--sdc',str(sdc),'--cst',str(cst),'--out',str(folder),'--timeout',str(int(budget.timeout(180,reserve=10))),'--no-iob-registers' if name=='rom' else '--output-iob-registers']
            with (out/(name+'.log')).open('w') as log:subprocess.run(cmd,stdout=log,stderr=subprocess.STDOUT,check=True,timeout=budget.remaining())
            r=audit(folder,dict(part='GW5AST-LV138FPG676AC1/I0',clock_periods_ns={'clk':5.}));(folder/'physical_audit.json').write_text(json.dumps(r,indent=2)+'\n')
            if pins(folder/'mapped.pin.html')!=expected:raise ValueError('Actual pin/electrical mismatch')
            paths=diagnose((folder/'mapped.tr').read_text());(folder/'routing_diagnosis.json').write_text(json.dumps(paths,indent=2)+'\n')
            row.update(status='complete',fmax=r['frequency_reports']['clk']['reported_fmax_mhz'],closed=r['timing_closed'],violations=r['violated_endpoints'],worst=paths[0]);print(name,row['fmax'],row['violations'],flush=True)
        except Exception as e:row.update(status='failed',error=repr(e));print(name,'FAILED',repr(e),flush=True)
        save()
    manifest.update(status='complete' if all(r['status']=='complete' for r in manifest['runs']) else 'completed_with_failures',wall_seconds=budget.used());save()
if __name__=='__main__':main()
