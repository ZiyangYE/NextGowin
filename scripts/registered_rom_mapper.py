"""Exact one-BSRAM implementation of a two-boundary registered Boolean core.

Bounded generic candidate: no design-name special cases, no added pipeline
latency, no vendor-mapped seed. Not a replacement for arbitrary state machines.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
from gowin_blif import read_blif
from registered_benchmark import rtl
from run_abc_reproduction import sha,ROOT
from yosys_runtime import SUITE,run,quote
from gowin_netlist_format import normalize

SDK=Path('C:/Gowin/Gowin_V1.9.12.04_x64/IDE/simlib/gw5a/prim_sim.v')

def table(network):
    ni,no=len(network['inputs']),len(network['outputs'])
    width=next((w for w in (1,2,4,8,16,32) if w>=no and (1<<ni)*w<=16384),None)
    if width is None or not 1<=ni<=14 or no<1:raise ValueError('Core does not fit one 16-Kbit pROM')
    nodes={n['output']:n for n in network['nodes']};values=[]
    for address in range(1<<ni):
        bits={n:(address>>i)&1 for i,n in enumerate(network['inputs'])}
        for name in network['topological']:
            n=nodes[name];index=sum(bits[b]<<i for i,b in enumerate(n['inputs']))
            bits[name]=(n['truth']>>index)&1
        values.append(sum(bits[b]<<i for i,b in enumerate(network['outputs'])))
    return width,values

def emit(network,registers='input'):
    width,values=table(network);ni,no=len(network['inputs']),len(network['outputs'])
    if registers not in ('input','output','internal'):raise ValueError('Unknown register placement')
    if registers!='input' and values[0]!=0:raise ValueError('Retimed zero-initialized ROM requires F(0)=0')
    memory=sum(v<<(i*width) for i,v in enumerate(values));shift=width.bit_length()-1
    address=[f'{14-ni-shift}\'b0'] if 14-ni-shift else []
    address+=['r' if registers=='input' else 'ai']+([f'{shift}\'b0'] if shift else [])
    lines=['module top(input clk,input ['+str(ni-1)+':0] a,output ['+str(no-1)+':0] q);',
        'wire ck,hi,lo;','wire ['+str(ni-1)+':0] ai,r;','wire [31:0] data;',f'wire [{no-1}:0] qr;',
        'IBUF clkbuf(.I(clk),.O(ck)); VCC vcc(.V(hi)); GND gnd(.G(lo)); GSR GSR(.GSRI(hi));']
    for i in range(ni):
        lines += [f'IBUF in_{i}(.I(a[{i}]),.O(ai[{i}]));']
        if registers=='input':lines.append(f'DFFRE #(.INIT(1\'b0)) launch_{i}(.D(ai[{i}]),.Q(r[{i}]),.CLK(ck),.CE(hi),.RESET(lo));')
    params=[f".READ_MODE(1'b{int(registers=='internal')})",f'.BIT_WIDTH({width})','.RESET_MODE("SYNC")']
    params += [f'.INIT_RAM_{i:02X}(256\'h{(memory>>(256*i))&((1<<256)-1):064X})' for i in range(64)]
    lines += ['pROM #(\n'+',\n'.join(params)+'\n) lookup(.CLK(ck),.CE(hi),.OCE(hi),.RESET(lo),.AD({'+','.join(address)+'}),.DO(data));']
    for i in range(no):
        if registers=='output':lines.append(f'DFFRE #(.INIT(1\'b0)) capture_{i}(.D(data[{i}]),.Q(qr[{i}]),.CLK(ck),.CE(hi),.RESET(lo));')
        lines.append(f'OBUF out_{i}(.I({"qr" if registers=="output" else "data"}[{i}]),.O(q[{i}]));')
    lines.append('endmodule')
    return '\n'.join(lines)+'\n',dict(inputs=ni,outputs=no,physical_width=width,truth_table_bits=(1<<ni)*no,bsram=1,latency_cycles=2,register_placement=registers)

def build(source,out,registers='input'):
    if os.name=='nt':
        import ctypes
        # Inherited by child tools: fail into logs instead of a modal loader UI.
        ctypes.windll.kernel32.SetErrorMode(0x8003)
    out.mkdir(parents=True,exist_ok=False);network=read_blif(source,max_inputs=14);code,record=emit(network,registers)
    source_v=out/'generated.v';source_v.write_text(code)
    run(['read_verilog -lib +/gowin/cells_sim.v','read_verilog -lib +/gowin/cells_xtra_gw5a.v','read_verilog '+quote(source_v),
         'hierarchy -check -top top','blackbox =A:whitebox','hilomap -hicell VCC V -locell GND G','clean -purge','splitnets','write_json '+quote(out/'readback.json'),'write_verilog -noattr -defparam '+quote(out/'raw.vg')],out,'emit',timeout=60)
    vg=out/'mapped.vg';formatted,formats=normalize((out/'raw.vg').read_text());vg.write_text(formatted)
    (out/'format.json').write_text(json.dumps(formats,indent=2)+'\n')
    ref=out/'reference.v';ref.write_text(rtl(network).replace('module top(', 'module reference('))
    ni,no=record['inputs'],record['outputs'];tb=out/'testbench.v'
    tb.write_text(f'''`timescale 1ns/1ps
module tb;
reg clk=0;reg [{ni-1}:0] a=0;wire [{no-1}:0] q,gold;
top dut(clk,a,q);reference refdesign(clk,a,gold);
always #5 clk=~clk;
integer i;
initial begin
 #1;if(q!==gold) $fatal(1,"initial state mismatch");
 for(i=0;i<{(1<<ni)+3};i=i+1) begin
  @(negedge clk);a=i;
  @(posedge clk);#1;if(q!==gold) $fatal(1,"cycle mismatch at %0d",i);
 end
 $display("EXHAUSTIVE_REGISTERED_ROM_PASS");$finish;
end
endmodule
''')
    env=dict(os.environ);env['PATH']=str(SUITE/'bin')+os.pathsep+str(SUITE/'lib')+os.pathsep+env.get('PATH','')
    image=out/'simulation.vvp';cmd=[str(SUITE/'bin/iverilog.exe'),'-B',str(SUITE/'lib/ivl'),'-g2012','-s','tb','-o',image.as_posix(),tb.as_posix(),ref.as_posix(),vg.as_posix(),SDK.as_posix()]
    with (out/'compile.log').open('w') as log:subprocess.run(cmd,stdout=log,stderr=subprocess.STDOUT,env=env,check=True,timeout=90)
    # Explicit VPI directory avoids vvp's unchecked Windows short-path lookup.
    cp=subprocess.run(['vvp.exe','-M','-','-M',str(SUITE/'lib/ivl'),str(image)],executable=str(SUITE/'bin/vvp.exe'),cwd=SUITE/'bin',capture_output=True,text=True,env=env,timeout=90)
    (out/'simulation.log').write_text(cp.stdout+cp.stderr)
    cp.check_returncode()
    if 'EXHAUSTIVE_REGISTERED_ROM_PASS' not in cp.stdout:raise ValueError('SDK simulation did not finish')
    record.update(status='verified',scope='Exact ROM truth table; exhaustive SDK simulation of actual emitted netlist versus registered BLIF RTL, initial state and two-cycle latency. No arbitrary sequential absorption.',source_sha256=sha(source),netlist_sha256=sha(vg),sdk_sha256=sha(SDK),simulation_vectors=(1<<ni)+3)
    (out/'manifest.json').write_text(json.dumps(record,indent=2)+'\n');return vg,record

if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('source',type=Path);ap.add_argument('--out',type=Path,required=True);ap.add_argument('--registers',choices=['input','output','internal'],default='input');a=ap.parse_args()
    _,r=build(a.source.resolve(),a.out.resolve(),a.registers);print(json.dumps(r,indent=2))
