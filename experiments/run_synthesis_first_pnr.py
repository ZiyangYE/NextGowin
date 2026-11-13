"""Physical stage after completed synthesis comparison; frozen netlists/pins."""
import argparse,json,re,subprocess,sys,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path[:0]=[str(ROOT/'scripts'),str(ROOT/'experiments')]
from project_timing import audit
from summarize_practical138 import coverage
from run_routing_ablation import pins
from routing_path_diagnosis import diagnose
from run_abc_reproduction import sha

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--out',type=Path,required=True);a=ap.parse_args();out=a.out.resolve();out.mkdir(exist_ok=False)
    for name in ('synthesis_wide_int157_001','synthesis_wide_uart143_001'):
        if json.loads((ROOT/'results'/name/'manifest.json').read_text())['status']!='complete':raise ValueError('Synthesis phase must finish first')
    # Snapshot the existing runner, changing only validated backend option
    # arguments. Production defaults and earlier method snapshots stay intact.
    original=ROOT/'scripts/run_gowin_netlist.py';text=original.read_text()
    marker="    args=ap.parse_args()"
    assert text.count(marker)==1
    text=text.replace(marker,"    ap.add_argument('--place-option',type=int,choices=range(5),default=3)\n    ap.add_argument('--route-option',type=int,choices=range(3),default=2)\n"+marker)
    marker="'-place_option 3','-route_option 2'";assert text.count(marker)==1
    text=text.replace(marker,"f'-place_option {args.place_option}',f'-route_option {args.route_option}'")
    runner=out/'physical_runner.py';runner.write_text('import sys\nsys.path.insert(0,'+repr(str(ROOT/'scripts'))+')\n'+text)
    base=ROOT/'results/synthesis_wide_int157_001';cfg=json.loads((ROOT/'results/int2float_fullio157_cold_001/config.json').read_text());cst=Path(cfg['cst'])
    reference=pins(ROOT/'results/general_synthesis_suite_012/int2float/vendor_pnr/mapped.pin.html')
    start=time.monotonic();rows=[];record=dict(status='running',scope='Frozen synthesis netlists; original package pins/electrical settings/full I/O. Native P&R settings and optional internal logic region only.',source_runner_sha256=sha(original),experiment_runner_sha256=sha(runner),rows=rows)
    def save():(out/'manifest.json').write_text(json.dumps(record,indent=2)+'\n')
    def case(recipe,target,place,route,region=False):
        name=f'{recipe}_{target}_p{place}r{route}'+('_region' if region else '')
        row=dict(name=name,recipe=recipe,target_mhz=target,place=place,route=route,internal_region=region,status='running');rows.append(row);save()
        source=base/recipe/'05_netlist/mapped.vg';f=out/name
        try:
            sdc=out/(name+'.sdc');s=Path(cfg['sdc']).read_text();s,n=re.subn(r'-period\s+[\d.]+',f'-period {1000/target:.9f}',s);assert n==1;sdc.write_text(s)
            used_cst=cst
            if region:
                paths=diagnose((base/recipe/'06_pnr/mapped.tr').read_text())
                p=next(p for p in paths if 'pad' not in p['source'] and 'pad' not in p['destination'])
                edge=next((e for e in p['edges'] if re.match(r'IOR\d+',e['destination_location'])),None)
                if edge is None:raise ValueError('No measured right-side IOB endpoint for region hypothesis')
                y=int(re.match(r'IOR(\d+)',edge['destination_location'])[1]);names={p['source']}
                for e in p['edges']:
                    for end in ('source','destination'):
                        loc=e[end+'_location'];cell=e[end].rsplit('/',1)[0]
                        if loc.startswith('R') and re.fullmatch(r'ng_\w+',cell):names.add(cell)
                used_cst=out/(name+'.cst');used_cst.write_text(cst.read_text()+''.join(f'INS_LOC "{n}" R[{y-3}:{y+3}]C[160:174];\n' for n in sorted(names)))
                row['region_cells']=sorted(names)
            source_hash=sha(source)
            with (out/(name+'.log')).open('w') as log:
                subprocess.run([sys.executable,str(runner),str(source),'--sdc',str(sdc),'--cst',str(used_cst),'--out',str(f),'--place-option',str(place),'--route-option',str(route),'--output-iob-registers','--bitstream','--timeout','180'],stdout=log,stderr=subprocess.STDOUT,check=True,timeout=min(190,1500-(time.monotonic()-start)))
            if sha(source)!=source_hash or sha(f/'mapped.vg')!=source_hash:raise ValueError('Synthesis netlist changed')
            if pins(f/'mapped.pin.html')!=reference:raise ValueError('Package pin/electrical assignment changed')
            p=audit(f,dict(part='GW5AST-LV138FPG676AC1/I0',clock_periods_ns={'clk':1000/target}));(f/'physical_audit.json').write_text(json.dumps(p,indent=2)+'\n')
            row.update(status='complete',closed=p['timing_closed'],violations=p['violated_endpoints'],frequency=p['frequency_reports'],resources=p['implementation'],coverage=coverage(f),netlist_sha256=source_hash,cst_sha256=sha(used_cst),critical_paths=diagnose((f/'mapped.tr').read_text()))
        except Exception as e:row.update(status='failed',error=repr(e))
        print(name,row['status'],row.get('closed'),row.get('violations'),flush=True);save();return row
    for recipe in ('lut4','wide'):
        for place,route in ((3,2),(3,1),(2,1),(4,1)):case(recipe,160,place,route)
        case(recipe,160,2,1,True)
    closed=[r for r in rows if r.get('closed')]
    if closed:
        best=max(closed,key=lambda r:r['frequency']['clk']['reported_fmax_mhz'])
        for target in (180,200):
            r=case(best['recipe'],target,best['place'],best['route'],best['internal_region'])
            if not r.get('closed'):break
    record.update(status='complete',wall_seconds=time.monotonic()-start,improvement_above_157mhz=bool(closed));save()
if __name__=='__main__':main()
