"""Common RTL harness and independent two-register-boundary equivalence proof."""
import json
import re
from pathlib import Path
from run_abc_reproduction import ROOT,cec,sha
from gw5a_functional_map import blif


def rtl(network):
    names=list(dict.fromkeys(network['inputs']+[n['output'] for n in network['nodes']]))
    bits={n:'n['+str(i)+']' for i,n in enumerate(names)}
    ni,no=len(network['inputs']),len(network['outputs'])
    lines=[f'module top(input clk,input [{ni-1}:0] a,output reg [{no-1}:0] q=0);',
           f'reg [{ni-1}:0] r=0;',f'wire [{len(names)-1}:0] n;']
    for i,n in enumerate(network['inputs']):lines.append(f'assign {bits[n]}=r[{i}];')
    for n in network['nodes']:
        k=len(n['inputs']);truth=n['truth'];width=1<<k
        expression=f"{width}'h{truth:X}"
        if k:expression='('+expression+' >> {'+','.join(bits[p] for p in reversed(n['inputs']))+'})'
        lines.append('assign '+bits[n['output']]+'='+expression+" & 1'b1;")
    lines.append('always @(posedge clk) begin r<=a;q<={'+','.join(bits[p] for p in reversed(network['outputs']))+'};end')
    return '\n'.join(lines+['endmodule'])+'\n'


def prove(design,reference,out):
    """Prove the combinational core, after checking exact FF control/state equations.

    Supports the registered benchmark contract only; rejects retiming, unknown
    sequential controls, RAMs or arbitrary IP instead of treating them as wires.
    """
    m=design['modules']['top'];cells=m['cells'];aliases={}
    for c in cells.values():
        ps=c['connections'];kind=c['type']
        if kind in ('IBUF','OBUF'):aliases[ps['O'][0]]=ps['I'][0]
        elif kind in ('VCC','GND'):
            b=ps['V' if kind=='VCC' else 'G'][0]
            if isinstance(b,int):aliases[b]='1' if kind=='VCC' else '0'
    def resolve(b):
        seen=set()
        while b in aliases:
            if b in seen:raise ValueError('Cyclic pad aliases')
            seen.add(b);b=aliases[b]
        return b
    def port(name,count):
        if name in m['ports']:
            row=m['ports'][name]
            if len(row['bits'])!=count:raise ValueError('Benchmark port width changed')
            return [resolve(b) for b in row['bits']]
        return [resolve(m['ports'][name+'['+str(i)+']']['bits'][0]) for i in range(count)]
    clock=resolve(m['ports']['clk']['bits'][0])
    inputs=port('a',len(reference['inputs']));outputs=port('q',len(reference['outputs']))
    input_names=dict(zip(inputs,reference['inputs']));mapped={};flops={};checked=[]
    for name,c in cells.items():
        if not c['type'].startswith('DFF'):continue
        if c['type'] not in ('DFFRE','DFFSE','DFFCE','DFFPE'):raise ValueError('Unproved FF type: '+c['type'])
        ps=c['connections'];init=c.get('parameters',{}).get('INIT','1' if c['type'] in ('DFFSE','DFFPE') else '0')
        if int(init,2)!=0 or resolve(ps['CLK'][0])!=clock:raise ValueError('FF clock or initial state changed')
        if any(resolve(ps[p][0])!='0' for p in ('CLEAR','PRESET') if p in ps):raise ValueError('Unproved asynchronous benchmark control')
        q=resolve(ps['Q'][0]);d=resolve(ps['D'][0]);flops[q]=(name,d,c)
        if d in input_names:
            if resolve(ps.get('CE',['1'])[0])!='1' or any(resolve(ps[p][0])!='0' for p in ('RESET','SET') if p in ps):raise ValueError('Launch register control changed')
            mapped[q]=input_names[d];checked.append(name)
    captures=[];next_states=[];extra_inputs=[]
    for b in outputs:
        if b=='0':captures.append('0');continue
        if b not in flops:raise ValueError('Output has no proven capture FF')
        name,d,c=flops[b]
        old='ng_capture_state_'+str(len(extra_inputs))
        if old in reference['inputs']+reference['outputs']:raise ValueError('Capture state name collision')
        extra_inputs.append(old);mapped[b]=old
        target='capture_next_'+str(len(next_states));captures.append(target);next_states.append((target,c,b));checked.append(name)
    if set(checked)!={n for n,c in cells.items() if c['type'].startswith('DFF')}:
        raise ValueError('Unclassified sequential state in benchmark')
    reserved=set(reference['inputs'])|set(reference['outputs']);prefix='ng_proof_'
    while any(n.startswith(prefix) for n in reserved):prefix+='x'
    def signal(b):
        b=resolve(b)
        return mapped.get(b,prefix+str(b))
    lines=['.model core','.inputs '+' '.join(reference['inputs']+extra_inputs),'.outputs '+' '.join(reference['outputs']),'.names '+signal('0'),'.names '+signal('1'),'1']
    def gate(ins,out,truth):
        k=len(ins);lines.append('.names '+' '.join([signal(b) for b in ins]+[signal(out)]))
        if k and truth==0:lines.append('-'*k+' 0');return
        for a in range(1<<k):
            if truth>>a&1:lines.append((''.join(str(a>>i&1) for i in range(k))+' 1') if k else '1')
    for name,c in cells.items():
        kind=c['type'];ps=c['connections']
        if kind in ('IBUF','OBUF','VCC','GND','GSR') or kind.startswith('DFF'):continue
        if re.fullmatch(r'LUT[1-4]',kind):
            gate([ps['I'+str(i)][0] for i in range(int(kind[-1]))],ps['F'][0],int(c['parameters']['INIT'],2))
        elif kind in ('MUX2','MUX2_LUT5','MUX2_LUT6','MUX2_LUT7','MUX2_LUT8'):
            gate([ps[p][0] for p in ('I0','I1','S0')],ps['O'][0],0xCA)
        elif kind=='INV':gate(ps['I'],ps['O'][0],1)
        elif kind=='ALU':
            mode=int(c['parameters']['ALU_MODE'],2)
            if mode not in range(9):raise ValueError('Unknown ALU mode')
            sums=carry=0
            for v in range(16):
                a,b,s,ci=[v>>i&1 for i in range(4)]
                propagate=[a^b,a^(1-b),(a^b) if s else a^(1-b),a^(1-b),a^(1-b),(1-a)^b,a,1-a,a if s else 1-a][mode]
                generate=1 if mode in (3,7) else 0 if mode==6 else b if mode==5 else a
                sums|=(propagate^ci)<<v;carry|=(ci if propagate else generate)<<v
            ins=[ps[p][0] for p in ('I0','I1','I3','CIN')]
            gate(ins,ps['SUM'][0],sums);gate(ins,ps['COUT'][0],carry)
        else:raise ValueError('Unproved combinational primitive: '+kind)
    for target,c,old in next_states:
        ps=c['connections'];control=ps.get('RESET',ps.get('SET',['0']))[0];truth=0
        for a in range(16):
            d,ce,ctrl,q=[a>>i&1 for i in range(4)]
            value=(1 if c['type']=='DFFSE' else 0) if ctrl else d if ce else q
            truth|=value<<a
        gate([ps['D'][0],ps.get('CE',['1'])[0],control,old],target,truth)
    for n,b in zip(reference['outputs'],captures):
        lines+=['.names '+signal(b)+' '+n,'1 1']
    target=out/'core_readback.blif';target.write_text('\n'.join(lines+['.end'])+'\n')
    extended=dict(reference,inputs=reference['inputs']+extra_inputs)
    (out/'reference_with_state.blif').write_text(blif(extended))
    proof=cec(ROOT/'build/abc/_TEST/abc.exe',out/'reference_with_state.blif',target,out/'core_cec.log',180)
    if not proof['equivalent']:raise RuntimeError('Benchmark full-core readback CEC failed')
    result=dict(cec=proof,flipflops_checked=len(set(checked)),readback_sha256=sha(target),
                scope='Full next-state CEC including synchronous reset/set and CE with unconstrained old capture state, plus exact zero-init/common-clock launch equations; no retiming.')
    (out/'equivalence.json').write_text(json.dumps(result,indent=2)+'\n')
    return result
