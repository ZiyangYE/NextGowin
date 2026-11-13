"""Same 3.3-V pins, explicit output drive alternatives; full I/O retained."""
import argparse,json,re,subprocess,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from project_timing import audit
from routing_path_diagnosis import diagnose
from run_routing_ablation import pins

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--out',type=Path,required=True);a=ap.parse_args();out=a.out.resolve();out.mkdir(exist_ok=False)
    result=dict(status='running',scope='3.3-V I/O, increased physical output drive, equal settings on both sides. Includes all original input/output timing; not a logic-only gain.',runs=[])
    def save():(out/'manifest.json').write_text(json.dumps(result,indent=2)+'\n')
    sources={'vendor':ROOT/'results/general_synthesis_suite_012/int2float/vendor_synthesis/mapped.vg','lut4':ROOT/'results/compact_output_ablation_001/int2float/05_netlist/mapped.vg'}
    expected=pins(ROOT/'results/general_synthesis_suite_012/int2float/vendor_pnr/mapped.pin.html')
    for drive in [16,24]:
        cst=out/f'drive{drive}.cst';cst.write_text((ROOT/'results/routing_ablation_002/int2float/fixed.cst').read_text().replace('DRIVE=8','DRIVE='+str(drive)))
        for name,vg in sources.items():
            row=dict(name=name,drive=drive,status='running');result['runs'].append(row);save();f=out/f'{name}_{drive}'
            try:
                with (out/f'{name}_{drive}.log').open('w') as log:subprocess.run([sys.executable,str(ROOT/'scripts/run_gowin_netlist.py'),str(vg),'--sdc',str(ROOT/'results/int2float_fullio_rom_001/clock200.sdc'),'--cst',str(cst),'--out',str(f),'--output-iob-registers','--timeout','180'],stdout=log,stderr=subprocess.STDOUT,check=True,timeout=190)
                p=audit(f,dict(part='GW5AST-LV138FPG676AC1/I0',clock_periods_ns={'clk':5.}));(f/'physical_audit.json').write_text(json.dumps(p,indent=2)+'\n')
                actual=pins(f/'mapped.pin.html');want={k:dict(v,drive=str(drive) if v['drive']=='8' else v['drive']) for k,v in expected.items()}
                if actual!=want:raise ValueError('Actual electrical or pin setting mismatch')
                row.update(status='complete',frequency=p['frequency_reports'],closed=p['timing_closed'],violations=p['violated_endpoints'],worst=diagnose((f/'mapped.tr').read_text())[0]);print(name,drive,row['frequency'],row['violations'],flush=True)
            except Exception as e:row.update(status='failed',error=repr(e));print(name,drive,'FAILED',repr(e),flush=True)
            save()
    result['status']='complete' if all(r['status']=='complete' for r in result['runs']) else 'completed_with_failures';save()
if __name__=='__main__':main()
