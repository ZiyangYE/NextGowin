"""Reconsider LUT/arithmetic boundaries with CEC and delay-costed cut mapping.

Memory, DSP, I/O and state remain named hard boundaries. ALUs are selectable
implementation alternatives, not mandatory cut boundaries. No RTL cycles change.
"""
import argparse,copy,json,time
from collections import Counter
from pathlib import Path
from native_boundary_cec import functions,prove
from ownpr_carry_chains import extract
from ownpr_place import sha
from ownpr_device import ROOT
from gw5a_functional_map import blif
from gw5a_arch_mapper import run
from workspace_budget import reserve


def boundary(design,arithmetic,preserved_heads=None):
    m=design['modules']['top'];cells=m['cells'];loads=Counter();observed=set()
    if arithmetic not in ('all','none','comparators','selected'):raise ValueError('Unknown arithmetic policy')
    if preserved_heads is not None and arithmetic!='selected':raise ValueError('Preserved heads require selected arithmetic policy')
    for row in m['ports'].values():observed.update(row['bits'])
    for row in m.get('netnames',{}).values():
        if int(row.get('attributes',{}).get('keep','0'),2) or 'init' in row.get('attributes',{}):observed.update(row['bits'])
    for c in cells.values():
        for pin,bs in c['connections'].items():
            if c['port_directions'][pin]=='input':loads.update(bs)
    alus=set()
    if arithmetic=='all':alus={n for n,c in cells.items() if c['type']=='ALU'}
    elif arithmetic=='selected':
        chains=extract(m)['chains'];known={c['cells'][0]:c for c in chains};heads=list(preserved_heads or [])
        if not heads or len(heads)!=len(set(heads)) or not set(heads)<=set(known):raise ValueError('Missing, duplicate or unknown preserved carry head')
        preserved={n for head in heads for n in known[head]['cells']}
        alus={n for n,c in cells.items() if c['type']=='ALU'}-preserved
    elif arithmetic=='comparators':
        for chain in extract(m)['chains']:
            if all(not loads[cells[n]['connections']['SUM'][0]] and cells[n]['connections']['SUM'][0] not in observed for n in chain['cells']):alus.update(chain['cells'])
    chosen={n for n,c in cells.items() if c['type'].startswith(('LUT','MUX2_LUT'))}|alus
    const={'0':0,'1':1};physical={}
    for c in cells.values():
        if c['type'] in ('GND','VCC'):
            b=next(iter(c['connections'].values()))[0];v=int(c['type']=='VCC');const[b]=v;physical.setdefault(v,b)
    if set(physical)!={0,1}:raise ValueError('Need explicit calibrated ground and power cells')
    outside=set(observed)
    for n,c in cells.items():
        if n not in chosen:
            for bs in c['connections'].values():outside.update(bs)
    def signal(b):
        if b in const:return 'c'+str(const[b])
        if not isinstance(b,int):raise ValueError('Undefined combinational signal')
        return 'b'+str(b)
    nodes=[];used=set();driven=set()
    for n in sorted(chosen):
        fs=functions(cells[n])
        if not fs:raise ValueError('Missing combinational function for '+n)
        for ins,o,t in fs:
            dest=signal(o)
            if dest in driven or dest in ('c0','c1'):raise ValueError('Multiple/constant logic driver')
            ins=list(map(signal,ins));nodes.append(dict(inputs=ins,output=dest,truth=t));used.update(ins);driven.add(dest)
    network=dict(inputs=sorted(used-driven-{'c0','c1'}),outputs=sorted(driven&{signal(b) for b in outside}),nodes=nodes+[dict(inputs=[],output='c0',truth=0),dict(inputs=[],output='c1',truth=1)])
    if not network['outputs']:raise ValueError('No observable remapping outputs')
    return chosen,physical,network,dict(arithmetic_policy=arithmetic,soft_alu_count=len(alus),preserved_alu_count=sum(c['type']=='ALU' for c in cells.values())-len(alus),preserved_chain_heads=sorted(preserved_heads or []))


def replace(design,chosen,constants,original,network):
    result=copy.deepcopy(design);m=result['modules']['top'];old=m['cells'];m['cells']={n:c for n,c in old.items() if n not in chosen};cells=m['cells']
    maximum=max([b for c in old.values() for bs in c['connections'].values() for b in bs if isinstance(b,int)]+[b for r in m.get('netnames',{}).values() for b in r['bits'] if isinstance(b,int)])
    bits={'c0':constants[0],'c1':constants[1]};bits.update({n:int(n[1:]) for n in original['inputs']+original['outputs']})
    def bit(n):
        nonlocal maximum
        if n not in bits:maximum+=1;bits[n]=maximum
        return bits[n]
    for i,row in enumerate(network['nodes']):
        if row['output'] in ('c0','c1'):continue
        if len(row['inputs'])>4:raise ValueError('Mapped ordinary LUT exceeds four inputs')
        name='ng_coopt_'+str(i)
        if name in cells:raise ValueError('Remapping cell collision')
        k=len(row['inputs']);truth=row['truth'];ins=[bit(n) for n in row['inputs']]
        if row.get('native_mux') is not None:
            if k!=3 or truth!=0xCA or row['native_mux'] not in (5,6):raise ValueError('Invalid mapped dedicated mux')
            cells[name]=dict(type='MUX2_LUT'+str(row['native_mux']),parameters={},attributes={},port_directions={'I0':'input','I1':'input','S0':'input','O':'output'},connections={p:[v] for p,v in zip(('I0','I1','S0','O'),ins+[bit(row['output'])])});continue
        if not k:k=1;ins=[constants[0]];truth=3 if truth else 0
        cells[name]=dict(type='LUT'+str(k),parameters={'INIT':format(truth,f'0{1<<k}b')},attributes={},port_directions={**{f'I{j}':'input' for j in range(k)},'F':'output'},
            connections={**{f'I{j}':[v] for j,v in enumerate(ins)},'F':[bit(row['output'])]})
    return result


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--netlist',type=Path,required=True);p.add_argument('--arithmetic',choices=('none','comparators','all','selected'),required=True);p.add_argument('--out',type=Path,required=True);p.add_argument('--recipe',choices=('lut4','wide','wide_pins','local_wide_pins'),default='lut4');p.add_argument('--selection',type=Path)
    p.add_argument('--feedback-checkpoint',type=Path);p.add_argument('--bidir-model',type=Path);a=p.parse_args()
    if bool(a.feedback_checkpoint)!=bool(a.bidir_model):p.error('Both native checkpoint and validated bidirectional model required')
    reserve(ROOT,100_000_000);start=time.monotonic();out=a.out.resolve();out.mkdir(exist_ok=False);old=json.loads(a.netlist.read_text());selected=json.loads(a.selection.read_text()) if a.selection else None
    if selected and selected['source_sha256']!=sha(a.netlist):raise ValueError('Arithmetic selection belongs to another source')
    chosen,constants,network,info=boundary(old,a.arithmetic,selected['preserved_chain_heads'] if selected else None)
    source=out/'source.blif';source.write_text(blif(network));constraints=out/'constraints.json'
    budgets=dict(units='ps',input_arrivals_ps={n:0 for n in network['inputs']},output_required_ps={n:5000 for n in network['outputs']});feedback=None
    if a.feedback_checkpoint:
        from ownpr_synthesis_boundary_budgets import constraints as native_constraints
        budgets,feedback=native_constraints(old,network,a.feedback_checkpoint,a.bidir_model)
        (out/'native_boundary_budgets.json').write_text(json.dumps(feedback,indent=2))
    constraints.write_text(json.dumps(budgets))
    model=ROOT/'results/timing/gowin138_model.json';mapped=run(source,constraints,model,out/'mapper',a.recipe)
    after=replace(old,chosen,constants,network,json.loads((out/'mapper/network.json').read_text()));proof=prove(old,after,out/'cec',120)
    (out/'netlist.json').write_text(json.dumps(after,indent=2));r=dict(status='equivalent_remapped_checkpoint',**info,proof=proof,source_sha256=sha(a.netlist),output_sha256=sha(out/'netlist.json'),
        before=dict(Counter(c['type'] for c in old['modules']['top']['cells'].values())),after=dict(Counter(c['type'] for c in after['modules']['top']['cells'].values())),wall_seconds=time.monotonic()-start,physical_timing_verified=False,state_and_hard_resources_preserved=True,native_boundary_feedback=feedback is not None)
    (out/'manifest.json').write_text(json.dumps(r,indent=2));print(json.dumps({k:v for k,v in r.items() if k!='proof'},indent=2))


if __name__=='__main__':main()
