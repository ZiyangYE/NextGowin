"""Matched CPU OREG ablation: unchanged RTL, package pins and full I/O contract."""
import json,subprocess,sys,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path[:0]=[str(ROOT/'scripts'),str(ROOT/'experiments')]
from project_timing import audit
from run_routing_ablation import pins
from summarize_practical138 import coverage
from routing_path_diagnosis import diagnose
from check_flow_checkpoint import check
from run_abc_reproduction import sha

def main():
    out=ROOT/'results/cpu_dsp_enable_ablation_001';out.mkdir(exist_ok=False)
    start=time.monotonic();rows=[];record=dict(status='running',scope='Fixed package pins, max/min external I/O delays, same interface cycles. No pin search. Tested operating points, not exact Fmax.',rows=rows)
    def save():(out/'manifest.json').write_text(json.dumps(record,indent=2)+'\n')
    def run(command,log,timeout=300):
        with log.open('w') as f:subprocess.run(command,stdout=f,stderr=subprocess.STDOUT,check=True,timeout=min(timeout,1200-(time.monotonic()-start)))
    new=ROOT/'results/cpu_dsp_inverted_cold_001/run/flow'
    cfg=json.loads((ROOT/'results/cpu_dsp_inverted_cold_001/config.json').read_text());cfg['pack_dsp_inverted_enable']=False
    config=out/'no_inverted_enable.json';config.write_text(json.dumps(cfg,indent=2)+'\n');save()
    try:
        run([sys.executable,str(ROOT/'scripts/run_frozen_flow.py'),'--config',str(config),'--out',str(out/'old_cold'),'--total-timeout','300'],out/'old_cold.log',310)
        old=out/'old_cold/flow'
        reference=ROOT/'results/tangprimer138_cpu_002/vexriscv/vendor_pnr100/mapped.pin.html'
        for name,folder,cp in [('old',old,out/'old_cold/input_config.json'),('packed',new,ROOT/'results/cpu_dsp_inverted_cold_001/run/input_config.json')]:
            check(folder,cp);f=folder/'06_pnr';p=audit(f,dict(part='GW5AST-LV138FPG676AC1/I0',clock_periods_ns={'clk':1000/82}))
            if pins(f/'mapped.pin.html')!=pins(reference):raise ValueError('Frozen I/O changed')
            rows.append(dict(name=name,target_mhz=82,kind='cold',physical_folder=str(f.relative_to(ROOT)),closed=p['timing_closed'],violations=p['violated_endpoints'],frequency=p['frequency_reports'],resources=p['implementation'],coverage=coverage(f),checkpoint_verified=True,critical_path=diagnose((f/'mapped.tr').read_text())[0]))
        save()
        sources=[('vendor',ROOT/'results/tangprimer138_cpu_002/vexriscv/vendor_synthesis/mapped.vg'),('old',old/'05_netlist/mapped.vg'),('packed',new/'05_netlist/mapped.vg')]
        source_sdc=(ROOT/'results/tangprimer138_cpu_002/vexriscv/clock100.sdc').read_text()
        if source_sdc.count('-period 10 ')!=1:raise ValueError('Unexpected full I/O SDC')
        for target in (90,85):
            sdc=out/f'fullio{target}.sdc';sdc.write_text(source_sdc.replace('-period 10 ',f'-period {1000/target:.9f} '))
            for name,vg in sources:
                row=dict(name=name,target_mhz=target,kind='fixed_netlist',status='running');rows.append(row);save()
                f=out/f'{name}{target}'
                try:
                    run([sys.executable,str(ROOT/'scripts/run_gowin_netlist.py'),str(vg),'--sdc',str(sdc),'--cst',cfg['cst'],'--out',str(f),'--output-iob-registers','--timeout','240'],out/f'{name}{target}.log',250)
                    p=audit(f,dict(part='GW5AST-LV138FPG676AC1/I0',clock_periods_ns={'clk':1000/target}));(f/'physical_audit.json').write_text(json.dumps(p,indent=2)+'\n')
                    if pins(f/'mapped.pin.html')!=pins(reference):raise ValueError('Frozen I/O changed')
                    row.update(status='complete',physical_folder=str(f.relative_to(ROOT)),closed=p['timing_closed'],violations=p['violated_endpoints'],frequency=p['frequency_reports'],resources=p['implementation'],coverage=coverage(f),critical_path=diagnose((f/'mapped.tr').read_text())[0],netlist_sha256=sha(f/'mapped.vg'),sdc_sha256=sha(f/'project.sdc'),cst_sha256=sha(f/'project.cst'))
                except Exception as e:row.update(status='failed',error=repr(e))
                print(name,target,row.get('closed'),row.get('violations'),flush=True);save()
        record['status']='complete'
    except Exception as e:record.update(status='failed',error=repr(e))
    record['wall_seconds']=time.monotonic()-start;save();print(record['status'],flush=True)
    if record['status']!='complete':raise SystemExit(1)
if __name__=='__main__':main()
