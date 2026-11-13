"""Fresh native RTL baseline against the cold full-I/O 157-MHz own run."""
import json,re,subprocess,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from project_timing import audit
from run_routing_ablation import pins
from registered_benchmark import prove
from gowin_blif import read_blif
from yosys_runtime import run,quote
from flow_budget import Budget,activate
from summarize_practical138 import coverage

def main():
    out=ROOT/'results/int2float_fresh_pair_001';out.mkdir(exist_ok=False);budget=Budget(300);activate(budget)
    cold=ROOT/'results/int2float_fullio157_cold_001/run/flow';reference=ROOT/'third_party/epfl/random_control/int2float.blif';network=read_blif(reference,max_inputs=11)
    proof=out/'own_proof';proof.mkdir();(proof/'reference.blif').write_bytes(reference.read_bytes());ownproof=prove(json.loads((cold/'05_netlist/final.json').read_text()),network,proof)
    sdc157=ROOT/'results/fullio_closure_003/int_lut4_local155_edge.sdc'
    with (out/'native_synthesis.log').open('w') as log:subprocess.run([sys.executable,str(ROOT/'scripts/native_project_synthesis.py'),'--frontend',str(cold/'01_frontend'),'--sdc',str(sdc157),'--out',str(out/'synthesis')],stdout=log,stderr=subprocess.STDOUT,check=True,timeout=budget.remaining())
    vg=out/'synthesis/mapped.vg';run(['read_verilog -lib +/gowin/cells_sim.v','read_verilog -lib +/gowin/cells_xtra_gw5a.v','read_verilog '+quote(vg),'hierarchy -check -top top','blackbox =A:whitebox','write_json '+quote(out/'vendor.json')],out,'import',60)
    proof=out/'vendor_proof';proof.mkdir();(proof/'reference.blif').write_bytes(reference.read_bytes());vendorproof=prove(json.loads((out/'vendor.json').read_text()),network,proof)
    clocks=set(re.findall(r'\.CLK\(([^)]+)\)',vg.read_text()))
    if len(clocks)!=1:raise ValueError('Unexpected clock set')
    cst=out/'fixed.cst';cst.write_text((ROOT/'results/routing_ablation_002/int2float/fixed.cst').read_text()+f'CLOCK_LOC "{clocks.pop().strip()}" LOCAL_CLOCK;\n')
    rows=[];expected=pins(ROOT/'results/general_synthesis_suite_012/int2float/vendor_pnr/mapped.pin.html')
    for freq in [157,149]:
        sdc=out/f'clock{freq}.sdc';sdc.write_text((ROOT/'results/routing_ablation_002/int2float/clock.sdc').read_text().replace('-period 10 ',f'-period {1000/freq:.9f} '));folder=out/f'pnr{freq}'
        with (out/f'pnr{freq}.log').open('w') as log:subprocess.run([sys.executable,str(ROOT/'scripts/run_gowin_netlist.py'),str(vg),'--sdc',str(sdc),'--cst',str(cst),'--out',str(folder),'--output-iob-registers','--timeout',str(int(budget.timeout(120,reserve=5)))],stdout=log,stderr=subprocess.STDOUT,check=True,timeout=budget.remaining())
        p=audit(folder,dict(part='GW5AST-LV138FPG676AC1/I0',clock_periods_ns={'clk':1000/freq}));(folder/'physical_audit.json').write_text(json.dumps(p,indent=2)+'\n')
        if pins(folder/'mapped.pin.html')!=expected:raise ValueError('Actual I/O mismatch')
        rows.append(dict(target=freq,frequency=p['frequency_reports'],closed=p['timing_closed'],violations=p['violated_endpoints'],coverage=coverage(folder)));print(rows[-1],flush=True)
    own=audit(cold/'06_pnr',dict(part='GW5AST-LV138FPG676AC1/I0',clock_periods_ns={'clk':1000/157}))
    if pins(cold/'06_pnr/mapped.pin.html')!=expected:raise ValueError('Cold own I/O mismatch')
    (out/'manifest.json').write_text(json.dumps(dict(status='complete',scope='Same original registered BLIF RTL, fresh native map-t baseline; same pins/electrical and LOCAL_CLOCK/output-IOB policy. Full I/O only.',vendor_runs=rows,own=dict(target=157,closed=own['timing_closed'],frequency=own['frequency_reports'],violations=own['violated_endpoints'],coverage=coverage(cold/'06_pnr')),own_full_core_proof=ownproof,vendor_full_core_proof=vendorproof,wall_seconds=budget.used()),indent=2)+'\n')
if __name__=='__main__':main()
