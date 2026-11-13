import json,os,subprocess,sys,ctypes
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from yosys_runtime import run,quote,SUITE
from gowin_netlist_format import normalize
from run_abc_reproduction import sha

def main():
    ctypes.windll.kernel32.SetErrorMode(0x8003)
    source=ROOT/'results/int2float_registered_rom_009';out=ROOT/'results/int2float_registered_rom_native_001';out.mkdir(exist_ok=False)
    run(['read_verilog -lib +/gowin/cells_sim.v','read_verilog -lib +/gowin/cells_xtra_gw5a.v','read_verilog '+quote(source/'mapped.vg'),'hierarchy -check -top top','blackbox =A:whitebox','splitnets -ports','write_verilog -noattr -defparam '+quote(out/'raw.vg')],out,'scalarize')
    code,record=normalize((out/'raw.vg').read_text());(out/'mapped.vg').write_text(code)
    tb=(source/'testbench.v').read_text();connections=['.clk(clk)']+[f'.\\a[{i}] (a[{i}])' for i in range(11)]+[f'.\\q[{i}] (q[{i}])' for i in range(7)]
    tb=tb.replace('top dut(clk,a,q);','top dut('+','.join(connections)+');');(out/'testbench.v').write_text(tb)
    env=dict(os.environ);env['PATH']=str(SUITE/'bin')+os.pathsep+str(SUITE/'lib')+os.pathsep+env['PATH']
    sdk=Path('C:/Gowin/Gowin_V1.9.12.04_x64/IDE/simlib/gw5a/prim_sim.v');image=out/'simulation.vvp'
    with (out/'compile.log').open('w') as log:subprocess.run([str(SUITE/'bin/iverilog.exe'),'-B',str(SUITE/'lib/ivl'),'-g2012','-s','tb','-o',str(image),str(out/'testbench.v'),str(source/'reference.v'),str(out/'mapped.vg'),str(sdk)],env=env,stdout=log,stderr=subprocess.STDOUT,check=True,timeout=90)
    cp=subprocess.run([str(SUITE/'bin/vvp.exe'),'-M','-','-M',str(SUITE/'lib/ivl'),str(image)],env=env,capture_output=True,text=True,timeout=90)
    (out/'simulation.log').write_text(cp.stdout+cp.stderr);cp.check_returncode()
    if 'EXHAUSTIVE_REGISTERED_ROM_PASS' not in cp.stdout:raise ValueError('simulation incomplete')
    (out/'manifest.json').write_text(json.dumps(dict(status='verified',netlist_sha256=sha(out/'mapped.vg'),parent_sha256=sha(source/'manifest.json'),sdk_sha256=sha(sdk),simulation_vectors=2051),indent=2)+'\n')
    print(cp.stdout)
if __name__=='__main__':main()
