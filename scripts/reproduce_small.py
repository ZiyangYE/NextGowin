"""Cold small-RTL demonstration using own mapping, placement and routing.

The shipped I/O reference is labelled vendor calibration. Run-time physical
implementation is our code; the vendor only checks STA and encodes its result.
"""
import argparse,json,subprocess,sys,time
from pathlib import Path
from workspace_budget import reserve
from yosys_runtime import run as yosys,quote
from map_resource_json import map_design
from nextgowin import materialize
from ownpr_place import ROOT,pack,place,constraints,sha
from ownpr_device import Device
from native_boundary_cec import prove


def main():
    p=argparse.ArgumentParser();p.add_argument('--out',type=Path,required=True);p.add_argument('--prepare-only',action='store_true');a=p.parse_args()
    start=time.monotonic();reserve(ROOT,1_500_000_000);out=a.out.resolve();out.mkdir(parents=True,exist_ok=False);example=ROOT/'examples/repro_small';source=example/'top.v'
    synth=out/'synthesis';synth.mkdir()
    yosys(['read_verilog -sv '+quote(source),'synth_gowin -top top -family gw5a -strict-gw5a-dffs -nowidelut -noiopads -run begin:map_luts',
           'blackbox =A:whitebox','check -assert -noinit','write_json '+quote(synth/'resources.json')],synth,'infer',120)
    map_design(synth/'resources.json',out/'mapping',50000,'arch_delay','lut4')
    materialize(out/'mapping/mapped.json',out/'emitted','top',optimize_resources=False)
    design=json.loads((out/'emitted/final.json').read_text());contract=constraints(design['modules']['top'],(example/'fixed.cst').read_text(),(example/'timing.sdc').read_text())
    packed,groups,constant,packing=pack(design,'top');placed=place(packed,'top',groups,constant,contract,Device(),ROOT/'results/native_route_runtime_007')
    folder=out/'placement';folder.mkdir();proof=prove(design,packed,folder/'cec',120)
    (folder/'packed.json').write_text(json.dumps(packed));(folder/'placement.json').write_text(json.dumps(placed,indent=2))
    (folder/'manifest.json').write_text(json.dumps(dict(status='placed',source_sha256=sha(out/'emitted/final.json'),netlist_sha256=sha(folder/'packed.json'),placement_sha256=sha(folder/'placement.json'),packing=packing,proof=proof),indent=2))
    if a.prepare_only:return
    seconds=900-int(time.monotonic()-start)
    if seconds<120:raise TimeoutError('Small example preparation exceeded its deadline')
    cmd=[sys.executable,str(ROOT/'scripts/run_ownpr_checkpoint.py'),'--placement',str(folder),'--cst',str(example/'fixed.cst'),'--sdc',str(example/'timing.sdc'),
         '--io-reference',str(ROOT/'results/native_small_repro_io_002'),'--search-bias','30','--root-arrival-weight','1','--guide-edge-delay','.05','--guided-corridor','--seconds',str(seconds),'--route-seconds','300','--out',str(out/'implementation')]
    subprocess.run(cmd,cwd=ROOT,check=True,timeout=seconds)
    result=json.loads((out/'implementation/manifest.json').read_text());metrics=result['metrics']
    selected=result['selected']
    if metrics['hold_violations']:
        state=out/'hold_source.json';state.write_text(json.dumps(dict(selected=selected),indent=2));remaining=900-int(time.monotonic()-start)
        cmd=[sys.executable,str(ROOT/'scripts/run_ownpr_hold_closure.py'),'--state',str(state),'--cst',str(example/'fixed.cst'),'--sdc',str(example/'timing.sdc'),
             '--io-reference',str(ROOT/'results/native_small_repro_io_002'),'--rounds','2','--count','4','--branch-limit','8','--seconds',str(remaining),'--out',str(out/'hold')]
        subprocess.run(cmd,cwd=ROOT,check=True,timeout=remaining)
        held=json.loads((out/'hold/manifest.json').read_text());metrics=held['metrics'];selected=held['selected']
    if not metrics['native_constraints_satisfied'] or not metrics['other_checks_passed'] or metrics['setup_violations'] or metrics['hold_violations']:raise ValueError('Small example did not meet complete I/O timing')
    record=dict(status='small_rtl_ownpr_reproduced',wall_seconds=time.monotonic()-start,budget_seconds=900,source_sha256=sha(source),metrics=metrics,selected=selected,
        vendor_pnr_executed=False,io_reference_is_vendor_calibration=True,device_programmed=False,board_wiring_validated=False)
    (out/'manifest.json').write_text(json.dumps(record,indent=2));print(json.dumps(record,indent=2))


if __name__=='__main__':main()
