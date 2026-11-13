"""Preserve native hard boxes while reconstructing a delay-rewritten AIG."""
import copy
import itertools
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from map_resource_json import extract,FUNCTIONS
from run_abc_reproduction import invoke,cec
from gowin_blif import read_blif
from gw5a_functional_map import reduce_function


def rewrite(design,out):
    design=copy.deepcopy(design);proofs=[];out.mkdir()
    for mn,m in design['modules'].items():
        cut=extract(m)
        if not cut:continue
        p=out/('module_'+str(len(proofs)));p.mkdir();source=p/'source.blif';source.write_text(cut['blif']);target=p/'rewritten.blif'
        phase='&get -n; &syn2 -d -R 0; &b -d; &put'
        r,_=invoke(ROOT/'build/abc/_TEST/abc.exe',f'read "{source.as_posix()}"; strash; {phase}; {phase}; strash; write_blif "{target.as_posix()}"; echo NEXTGOWIN_DONE',p/'rewrite.log',300)
        if r['status']!='ok':raise RuntimeError('AIG rewriting failed')
        proof=cec(ROOT/'build/abc/_TEST/abc.exe',source,target,p/'cec.log',300)
        if not proof['equivalent']:raise ValueError('Rewritten AIG boundary contract failed')
        n=read_blif(target,max_inputs=2)
        if len(n['inputs'])!=len(cut['inputs']) or len(n['outputs'])!=len(cut['outputs']):raise ValueError('AIG interface changed')
        bits={name:int(old[1:]) for names,olds in [(n['inputs'],cut['inputs']),(n['outputs'],cut['outputs'])] for name,old in zip(names,olds)}
        maximum=max((b for c in m['cells'].values() for bs in c['connections'].values() for b in bs if isinstance(b,int)),default=1)
        maximum=max(maximum,max((b for r in list(m.get('netnames',{}).values())+list(m.get('ports',{}).values()) for b in r['bits'] if isinstance(b,int)),default=1))
        def bit(name):
            nonlocal maximum
            if name not in bits:maximum+=1;bits[name]=maximum
            return bits[name]
        cells={name:c for name,c in m['cells'].items() if name not in cut['gates']}
        for i,node in enumerate(n['nodes']):
            ins,truth=reduce_function(tuple(node['inputs']),node['truth'])
            if not ins:
                pins='A';kind='$_BUF_';inputs=['1' if truth else '0']
            else:
                selected=None
                for kind,(pins,fun) in FUNCTIONS.items():
                    if len(pins)!=len(ins):continue
                    for perm in itertools.permutations(range(len(ins))):
                        actual=sum(int(fun(*[(a>>j)&1 for j in perm]))<<a for a in range(1<<len(ins)))
                        if actual==truth:selected=(kind,pins,[bit(ins[j]) for j in perm]);break
                    if selected:break
                if selected is None:raise ValueError('AIG reconstruction encountered an unsupported truth function')
                kind,pins,inputs=selected
            name='ng_preabc9_'+str(i)
            if name in cells:raise ValueError('AIG gate namespace collision')
            cells[name]=dict(type=kind,parameters={},attributes={},connections={**{p:[b] for p,b in zip(pins,inputs)},'Y':[bit(node['output'])]},
                             port_directions={**{p:'input' for p in pins},'Y':'output'})
        m['cells']=cells;proofs.append(dict(module=mn,proof=proof))
    return design,proofs
