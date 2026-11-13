"""Check functional extraction through actual native VG simulation and P&R."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
from gw5a_functional_map import lower,blif
from map_resource_json import replace,readback
from nextgowin import materialize
from gowin_netlist_format import LIBRARY
from yosys_runtime import SUITE
from run_abc_reproduction import ROOT,cec,sha


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--out',type=Path,required=True);ap.add_argument('--late-select',action='store_true');args=ap.parse_args()
    out=args.out.resolve();out.mkdir(parents=True,exist_ok=False)
    truths=[sum(((a>>((a>>4)&3))&1)<<a for a in range(64)),0xA31985E7642B0DF8,0x8000000080000000]
    network=dict(inputs=['b'+str(i) for i in range(2,8)],outputs=['b9','b11','b12'],
                 nodes=[dict(inputs=['b'+str(i) for i in range(2,8)],output='b'+str(o),truth=t) for o,t in zip((9,11,12),truths)],topological=['b9','b11','b12'])
    mapped,record=lower(network,{'b7':5000} if args.late_select else None);(out/'choices.json').write_text(json.dumps(record,indent=2)+'\n')
    cells={}
    def ff(n,d,q):
        cells[n]=dict(type='DFFRE',parameters={'INIT':'0'},attributes={},port_directions=dict(D='input',Q='output',CLK='input',CE='input',RESET='input'),connections=dict(D=[d],Q=[q],CLK=[8],CE=['1'],RESET=['0']))
    for i in range(5 if args.late_select else 6):ff('launch'+str(i),20+i,2+i)
    for i,d in enumerate((9,11,12)):ff('capture'+str(i),d,30+i)
    module=dict(attributes={},netnames={},ports=dict(clk=dict(direction='input',bits=[8]),a=dict(direction='input',bits=list(range(20,26))),q=dict(direction='output',bits=[30,31,32])),cells=cells)
    if args.late_select:
        module['ports']['a']['bits']=list(range(20,25));module['ports']['s']=dict(direction='input',bits=[7])
    cut=dict(gates={},inputs=network['inputs'],outputs=network['outputs'])
    module=replace(module,cut,mapped);(out/'input.json').write_text(json.dumps(dict(modules={'top':module}))+'\n')
    (out/'source.blif').write_text(blif(network));(out/'readback.blif').write_text(readback(module,cut))
    proof=cec(ROOT/'build/abc/_TEST/abc.exe',out/'source.blif',out/'readback.blif',out/'cec.log',60)
    if not proof['equivalent']:raise RuntimeError('Architecture JSON CEC failed')
    materialize(out/'input.json',out/'emit','top')
    design=json.loads((out/'emit/final.json').read_text());pins=[]
    for name,row in design['modules']['top']['ports'].items():
        formal='\\'+name+' ' if '[' in name else name
        pins.append('.'+formal+'('+('a[5]' if name=='s' else name)+')')
    tb='''module tb;reg clk=0;reg [5:0] a=0;wire [2:0] q;GSR GSR(.GSRI(1'b1));
top dut('''+', '.join(pins)+''');
integer i,previous=0;reg [2:0] expected;
initial begin #1;if(q!==0)$fatal(1,"initial mismatch");
for(i=0;i<128;i=i+1)begin clk=0;a=i%64;#2;clk=1;#2;
'''+''.join(f"expected[{i}]=(64'h{t:016X}>>{'((previous&31)|(a&32))' if args.late_select else 'previous'})&1;\n" for i,t in enumerate(truths))+'''
if(q!==expected)$fatal(1,"native VG mismatch %0d",i);previous=a;
end $display("PASS native architecture VG: 128 cycles");$finish;end endmodule'''
    (out/'tb.v').write_text(tb);env=dict(os.environ);env['PATH']=str(SUITE/'lib')+os.pathsep+env.get('PATH','')
    commands=[[str(SUITE/'bin/iverilog.exe'),'-B',str(SUITE/'lib/ivl'),'-g2012','-s','tb','-o',str(out/'sim'),str(out/'tb.v'),str(out/'emit/mapped.vg'),str(LIBRARY)],
              [str(SUITE/'bin/vvp.exe'),'-M-','-M'+str(SUITE/'lib/ivl'),str(out/'sim')]]
    for i,cmd in enumerate(commands):
        cp=subprocess.run(cmd,env=env,capture_output=True,text=True,timeout=60);(out/f'simulation_{i}.log').write_text(cp.stdout+cp.stderr)
        if cp.returncode:raise RuntimeError('Actual native VG simulation failed')
    if 'PASS native architecture VG' not in cp.stdout:raise RuntimeError('Incomplete simulation')
    (out/'clock.sdc').write_text('create_clock -name clk -period 10 [get_ports {clk}]\nset_clock_uncertainty -hold 0.050 -from [get_clocks {clk}] -to [get_clocks {clk}]\n')
    if args.late_select:
        with (out/'clock.sdc').open('a') as stream:stream.write('set_input_delay -max 5 -clock clk [get_ports {s}]\nset_input_delay -min 0 -clock clk [get_ports {s}]\n')
    with (out/'native.log').open('w') as log:
        subprocess.run([sys.executable,str(ROOT/'scripts/run_gowin_netlist.py'),str(out/'emit/mapped.vg'),'--sdc',str(out/'clock.sdc'),'--out',str(out/'pnr'),'--timeout','180'],stdout=log,stderr=subprocess.STDOUT,check=True,timeout=200)
    result=dict(status='passed',cec=proof,cycles=128,native_model_sha256=sha(LIBRARY),netlist_sha256=sha(out/'emit/mapped.vg'),scope='Actual LUT4/MUX5/MUX6 and constant-cofactor VG simulation plus native structural P&R. Not a benchmark superiority result.')
    (out/'manifest.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(dict(status='passed',cycles=128)))


if __name__=='__main__':main()
