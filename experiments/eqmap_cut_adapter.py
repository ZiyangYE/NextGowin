"""Run upstream EqMap on a combinational cut; independently read back truth.

Experimental adapter: no sequential cells, retiming, vendor-synthesized seed,
or assumption that Xilinx LUT6 is a single physical GW5A LUT. Wider results
are decomposed into verified GW5A LUT4/private-MUX structures afterwards.
"""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
from gowin_blif import read_blif
from gw5a_functional_map import blif,lower
from run_abc_reproduction import sha,cec
from yosys_runtime import run as yosys,quote

BINARY=ROOT/'build/eqmap_target/release/eqmap_fpga.exe'
SOURCE=ROOT/'third_party/eqmap'


def emit(network):
    names=list(dict.fromkeys(network['inputs']+[n['output'] for n in network['nodes']]))
    bits={n:'w'+str(i) for i,n in enumerate(names)}
    inputs=['a'+str(i) for i in range(len(network['inputs']))]
    outputs=['z'+str(i) for i in range(len(network['outputs']))]
    rows=['module top('+','.join(inputs+outputs)+');']
    rows+=['input '+p+';' for p in inputs]+['output '+p+';' for p in outputs]
    rows+=['wire '+p+';' for p in bits.values()]
    rows+=['assign '+bits[n]+' = '+p+';' for n,p in zip(network['inputs'],inputs)]
    for i,node in enumerate(network['nodes']):
        k=len(node['inputs']);width=1<<k
        if not k:
            rows.append('assign '+bits[node['output']]+" = 1'b"+str(node['truth'])+';')
            continue
        if k>6:raise ValueError('EqMap adapter supports at most six-input LUTs')
        pins=[f'.I{j}({bits[p]})' for j,p in enumerate(node['inputs'])]+['.O('+bits[node['output']]+')']
        rows.append(f"LUT{k} #(.INIT({width}'h{node['truth']:X})) cell{i}("+','.join(pins)+');')
    rows+=['assign '+p+' = '+bits[n]+';' for p,n in zip(outputs,network['outputs'])]
    return '\n'.join(rows+['endmodule'])+'\n'


def readback(design,reference):
    module=design['modules']['top'];ports=module['ports'];bindings={};nodes=[]
    prefix='eq_'
    while any(n.startswith(prefix) for n in reference['inputs']+reference['outputs']):prefix+='x'
    for i,name in enumerate(reference['inputs']):
        row=ports['a'+str(i)]
        if row['direction']!='input' or len(row['bits'])!=1:raise ValueError('EqMap changed input contract')
        bit=row['bits'][0]
        if bit in bindings and bindings[bit]!=name:raise ValueError('EqMap merged independent inputs')
        bindings[bit]=name
    # Name a driven signal by its existing cut output where possible. This
    # avoids introducing artificial LUT1 buffers on every observed boundary.
    for i,name in enumerate(reference['outputs']):
        bits=ports['z'+str(i)]['bits']
        if len(bits)==1 and isinstance(bits[0],int):bindings.setdefault(bits[0],name)
    def signal(bit):
        if bit in ('x','z'):raise ValueError('EqMap introduced undefined logic')
        if bit in ('0','1'):return prefix+'const_'+bit
        return bindings.get(bit,prefix+'internal_'+str(bit))
    simple={'AND':('AB','Y',8),'AND2':('AB','Y',8),'NAND':('AB','Y',7),'NAND2':('AB','Y',7),
            'NOR':('AB','Y',1),'NOR2':('AB','Y',1),'OR':('AB','Y',14),'OR2':('AB','Y',14),
            'XOR':('AB','Y',6),'XOR2':('AB','Y',6),'NOT':('A','Y',1),'INV':('I','O',1),
            'MUX':(('A','B','S'),'Y',0xAC)}
    for cell in module.get('cells',{}).values():
        kind=cell['type'];pins=cell['connections']
        if kind.startswith('LUT') and kind[3:].isdigit() and 1<=int(kind[3:])<=6:
            ins=['I'+str(j) for j in range(int(kind[3:]))];output='O';truth=int(cell['parameters']['INIT'],2)
        elif kind in simple:ins,output,truth=simple[kind]
        elif kind in ('GND','VCC'):
            ins=[];output='G' if kind=='GND' else 'P';truth=int(kind=='VCC')
        else:raise ValueError('Unsupported or sequential EqMap output cell: '+kind)
        if any(len(pins[p])!=1 for p in list(ins)+[output]):raise ValueError('Unexpected EqMap cell width')
        nodes.append(dict(inputs=[signal(pins[p][0]) for p in ins],output=signal(pins[output][0]),truth=truth))
    nodes.extend(dict(inputs=[],output=prefix+'const_'+str(v),truth=v) for v in (0,1))
    for i,name in enumerate(reference['outputs']):
        row=ports['z'+str(i)]
        if row['direction']!='output' or len(row['bits'])!=1:raise ValueError('EqMap changed output contract')
        driver=signal(row['bits'][0])
        if driver!=name:nodes.append(dict(inputs=[driver],output=name,truth=2))
    expected={'a'+str(i) for i in range(len(reference['inputs']))}|{'z'+str(i) for i in range(len(reference['outputs']))}
    if set(ports)!=expected:raise ValueError('EqMap changed port set')
    return dict(inputs=reference['inputs'],outputs=reference['outputs'],nodes=nodes)


def optimize(source,out,min_depth=False,timeout=90):
    source,out=Path(source).resolve(),Path(out).resolve();out.mkdir(parents=True,exist_ok=False)
    start=time.perf_counter();network=read_blif(source,max_inputs=6)
    rtl=out/'input.v';rtl.write_text(emit(network));target=out/'mapped.v'
    command=[str(BINARY),str(rtl),str(target),'--report',str(out/'upstream_report.json'),
             '--partition','r2r','-k','4','-t','10','-s','96000','-n','12']
    if min_depth:command.append('--min-depth')
    with (out/'eqmap.log').open('w') as log:
        subprocess.run(command,stdout=log,stderr=subprocess.STDOUT,check=True,timeout=timeout)
    # Read declarations only. Truth is decoded below independently of the
    # upstream Verilog simulator; unknown cells and state are rejected.
    constants=out/'constants.v';constants.write_text('module GND(output G);endmodule\nmodule VCC(output P);endmodule\n')
    yosys(['read_verilog -lib '+quote(SOURCE/'verilog/simlib.v'),'read_verilog -lib '+quote(constants),
           'read_verilog '+quote(target),'hierarchy -check -top top','write_json '+quote(out/'actual.json')],out,'import')
    actual=readback(json.loads((out/'actual.json').read_text()),network)
    readback_path=out/'readback.blif';readback_path.write_text(blif(actual))
    actual=read_blif(readback_path,max_inputs=6)
    cost=json.loads((ROOT/'configs/gw5a_arch_cost_v2.json').read_text())
    legal,architecture=lower(actual,{p:0 for p in network['inputs']},lut_ps=cost['lut_ps'],cut_wire_ps=cost['cut_wire_ps'])
    (out/'network.json').write_text(json.dumps(legal)+'\n')
    final=out/'mapped.blif';final.write_text(blif(legal))
    proof=cec(ROOT/'build/abc/_TEST/abc.exe',source,final,out/'cec.log',180)
    if not proof['equivalent']:raise ValueError('Actual EqMap truth/decomposition failed independent CEC')
    commit=subprocess.check_output(['git','-c','safe.directory='+SOURCE.as_posix(),'-C',str(SOURCE),'rev-parse','HEAD'],text=True).strip()
    report=dict(status='proved',upstream_commit=commit,upstream_binary_sha256=sha(BINARY),
                source_sha256=sha(source),adapter_sha256=sha(Path(__file__).resolve()),command=command,
                min_depth=min_depth,proof=proof,architecture=architecture,wall_seconds=time.perf_counter()-start,
                scope='Combinational EqMap adaptation only; physical superiority and complete paper reproduction not established.')
    (out/'manifest.json').write_text(json.dumps(report,indent=2)+'\n')
    return report,legal


if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('source',type=Path);ap.add_argument('--out',type=Path,required=True)
    ap.add_argument('--min-depth',action='store_true');args=ap.parse_args()
    r,_=optimize(args.source,args.out,args.min_depth);print({k:v for k,v in r.items() if k not in ('architecture','command')})
