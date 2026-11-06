"""Strict combinational BLIF-to-Gowin LUT emitter and independent INIT readback.

Registers are added at all input/output boundaries. Unsupported BLIF constructs
are rejected. INIT readback reconstructs the combinational network for ABC CEC.
"""
import argparse
from collections import defaultdict,deque
import hashlib
import json
from pathlib import Path
import re


def cube_truth(inputs, cover, max_inputs=4):
    n = len(inputs)
    if n>max_inputs:
        raise ValueError(f'BLIF node exceeds the supported {max_inputs} inputs')
    if not cover:
        return 0
    polarities = {row[-1] for row in cover}
    if not polarities <= {'0','1'} or len(polarities)!=1:
        raise ValueError('Mixed or invalid BLIF cover polarity')
    polarity = int(next(iter(polarities)))
    result = 0 if polarity else (1<<(1<<n))-1
    for row in cover:
        if n==0:
            if len(row)!=1:
                raise ValueError('Invalid constant cover')
            pattern=''
        else:
            if len(row)!=2 or len(row[0])!=n or set(row[0])-set('01-'):
                raise ValueError('Invalid cube width/characters')
            pattern=row[0]
        for assignment in range(1<<n):
            if all(c=='-' or int(c)==((assignment>>i)&1) for i,c in enumerate(pattern)):
                if polarity:
                    result |= 1<<assignment
                else:
                    result &= ~(1<<assignment)
    return result


def read_blif(path,max_inputs=4):
    text = re.sub(r'\\\s*\r?\n',' ',path.read_text())
    inputs, outputs, nodes, current = [],[],[],None
    for raw in text.splitlines():
        line = raw.split('#',1)[0].strip()
        if not line:
            continue
        words=line.split()
        if words[0]=='.inputs':
            inputs.extend(words[1:]); current=None
        elif words[0]=='.outputs':
            outputs.extend(words[1:]); current=None
        elif words[0]=='.names':
            if len(words)<2:
                raise ValueError('Empty .names')
            current=dict(inputs=words[1:-1],output=words[-1],cover=[])
            nodes.append(current)
        elif words[0] in ('.model','.end'):
            current=None
        elif words[0].startswith('.'):
            raise ValueError(f'Unsupported BLIF directive: {words[0]}')
        elif current is None:
            raise ValueError('Cover without .names')
        else:
            current['cover'].append(words)
    drivers=set(inputs)
    if len(drivers)!=len(inputs) or len(set(outputs))!=len(outputs):
        raise ValueError('Duplicate BLIF ports')
    for node in nodes:
        if node['output'] in drivers:
            raise ValueError('Multiple net drivers')
        drivers.add(node['output'])
        node['truth']=cube_truth(node['inputs'],node['cover'],max_inputs)
    if set(outputs)-drivers or any(set(n['inputs'])-drivers for n in nodes):
        raise ValueError('Undriven BLIF signal')
    # Topological validation avoids Python recursion limits on deep datapaths.
    by_name={n['output']:n for n in nodes}
    fanouts=defaultdict(list);pending={}
    for net,node in by_name.items():
        dependencies=set(node['inputs']) & by_name.keys()
        pending[net]=len(dependencies)
        for parent in dependencies:
            fanouts[parent].append(net)
    ready=deque(net for net,count in pending.items() if count==0)
    order=[]
    while ready:
        net=ready.popleft();order.append(net)
        for sink in fanouts[net]:
            pending[sink]-=1
            if pending[sink]==0:
                ready.append(sink)
    if len(order)!=len(nodes):
        raise ValueError('Combinational cycle')
    return dict(inputs=inputs,outputs=outputs,nodes=nodes,topological=order)


def emit(network):
    if not network['inputs'] or not network['outputs']:
        raise ValueError('Nonempty input/output boundaries required')
    nets={name:f'n{i}' for i,name in enumerate(network['inputs']+[n['output'] for n in network['nodes']])}
    ni,no=len(network['inputs']),len(network['outputs'])
    lines=['module top(clk,a,q);','input clk;',f'input [{ni-1}:0] a;',f'output [{no-1}:0] q;',
        'wire ck,vcc,gnd;','wire '+','.join(nets.values())+';',
        'wire '+','.join(f'ai{i}' for i in range(ni))+';',
        'wire '+','.join(f'qr{i}' for i in range(no))+';',
        'IBUF clk_ibuf(.I(clk),.O(ck));','VCC vc(.V(vcc));','GND gc(.G(gnd));','GSR gs(.GSRI(vcc));']
    for i,port in enumerate(network['inputs']):
        lines += [f'IBUF in{i}(.I(a[{i}]),.O(ai{i}));',
            f'DFFRE launch{i}(.D(ai{i}),.CLK(ck),.CE(vcc),.RESET(gnd),.Q({nets[port]}));',
            f"defparam launch{i}.INIT=1'b0;"]
    for i,node in enumerate(network['nodes']):
        k=len(node['inputs']);signal=nets[node['output']]
        if k==0:
            lines.append(f'VCC const{i}(.V({signal}));' if node['truth'] else f'GND const{i}(.G({signal}));')
        else:
            ports=','.join(f'.I{j}({nets[p]})' for j,p in enumerate(node['inputs']))
            digits=max(1,(1<<k)//4)
            lines += [f'LUT{k} lut{i}({ports},.F({signal}));',f"defparam lut{i}.INIT={1<<k}'h{node['truth']:0{digits}X};"]
    for i,port in enumerate(network['outputs']):
        lines += [f'DFFRE capture{i}(.D({nets[port]}),.CLK(ck),.CE(vcc),.RESET(gnd),.Q(qr{i}));',
            f"defparam capture{i}.INIT=1'b0;",f'OBUF out{i}(.I(qr{i}),.O(q[{i}]));']
    return '\n'.join(lines+['endmodule'])+'\n'


def readback(verilog):
    cells=[]
    for kind,name,body in re.findall(r'\b(LUT[1-4]|DFFRE|VCC|GND)\s+(\w+)\s*\(([^;]+)\);',verilog):
        ports=dict(re.findall(r'\.(\w+)\(([^)]+)\)',body))
        cells.append((kind,name,ports))
    launches=sorted([(int(name[6:]),ports['Q']) for kind,name,ports in cells if kind=='DFFRE' and name.startswith('launch')])
    captures=sorted([(int(name[7:]),ports['D']) for kind,name,ports in cells if kind=='DFFRE' and name.startswith('capture')])
    if not launches or not captures:
        raise ValueError('Missing sequential boundary')
    for kind,name,ports in cells:
        if kind=='DFFRE' and (ports.get('CLK')!='ck' or ports.get('CE')!='vcc' or ports.get('RESET')!='gnd'):
            raise ValueError('Unexpected sequential controls')
    lines=['.model readback','.inputs '+' '.join(n for _,n in launches),'.outputs '+' '.join('o'+str(i) for i,_ in captures)]
    for kind,name,ports in cells:
        if kind in ('VCC','GND'):
            lines.append('.names '+ports['V' if kind=='VCC' else 'G'])
            if kind=='VCC':
                lines.append('1')
        elif kind.startswith('LUT'):
            k=int(kind[-1])
            match=re.search(rf'defparam\s+{re.escape(name)}\.INIT=(\d+)\x27h([0-9A-Fa-f]+);',verilog)
            if not match or int(match[1])!=1<<k:
                raise ValueError('Missing/wrong INIT width')
            value=int(match[2],16)
            if value.bit_length()>(1<<k):
                raise ValueError('INIT exceeds primitive width')
            lines.append('.names '+' '.join([ports['I'+str(i)] for i in range(k)]+[ports['F']]))
            for assignment in range(1<<k):
                if (value>>assignment)&1:
                    lines.append(''.join(str((assignment>>i)&1) for i in range(k))+' 1')
    for i,signal in captures:
        lines.extend([f'.names {signal} o{i}','1 1'])
    return '\n'.join(lines+['.end'])+'\n'


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('input',type=Path)
    ap.add_argument('--out',type=Path,required=True)
    args=ap.parse_args()
    network=read_blif(args.input)
    args.out.mkdir(parents=True,exist_ok=False)
    text=emit(network)
    (args.out/'mapped.vg').write_text(text)
    (args.out/'readback.blif').write_text(readback(text))
    manifest=dict(scope='LUT4 combinational mapping with explicit register boundaries; readback CEC still required',
        source_sha256=hashlib.sha256(args.input.read_bytes()).hexdigest(),
        mapped_sha256=hashlib.sha256(text.encode()).hexdigest(),inputs=len(network['inputs']),outputs=len(network['outputs']),
        lut_nodes=sum(bool(n['inputs']) for n in network['nodes']))
    (args.out/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    print(json.dumps(manifest,indent=2))


if __name__=='__main__':
    main()
