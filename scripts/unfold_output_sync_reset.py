"""Fold synchronous reset into a private data LUT to enable static IOREG.

No state, clock, enable, initialization or interface cycle is changed. This
direction complements control extraction; final physical timing decides it.
"""
import argparse,collections,copy,json,re,time
from pathlib import Path
from ownpr_device import ROOT
from ownpr_image_storage import load_json,sha
from ownpr_ioreg_macros import site_for
from ownpr_native_netlist import emit
from audit_official_source_equivalence import formal_library,proof_commands
from yosys_runtime import run
from workspace_budget import reserve


def transform(design,placed):
    new=copy.deepcopy(design);cells=new['modules']['top']['cells'];original=design['modules']['top']['cells'];constants={'0':0,'1':1};loads=collections.defaultdict(list);drivers={}
    for n,c in cells.items():
        for pin,bits in c['connections'].items():
            for bit in bits:
                if c['port_directions'][pin]=='output':drivers[bit]=n
                else:loads[bit].append((n,pin))
        if c['type'] in ('GND','VCC'):constants[next(iter(c['connections'].values()))[0]]=int(c['type']=='VCC')
    grounds=[c['connections']['G'][0] for c in cells.values() if c['type']=='GND']
    if len(grounds)!=1:raise ValueError('Expected one canonical physical ground')
    def kept(attrs):
        return any(str(attrs.get(k,'0')).lower() not in ('0','false','no','') and (int(str(attrs[k]),2) if set(str(attrs[k]))<=set('01') else True) for k in ('keep','dont_touch','syn_keep','syn_preserve') if k in attrs)
    protected={b for p in design['modules']['top']['ports'].values() for b in p['bits']}
    protected.update(b for p in design['modules']['top'].get('netnames',{}).values() if kept(p.get('attributes',{})) for b in p['bits'])
    changes=[]
    for io,location in placed['fixed_io'].items():
        if cells[io]['type']!='OBUF':continue
        try:site_for(location)
        except ValueError:continue
        ffname=drivers.get(cells[io]['connections']['I'][0]);ff=cells.get(ffname,{})
        if ff.get('type')!='DFFRE' or kept(ff.get('attributes',{})):continue
        pins=ff['connections'];q=pins['Q'][0];reset=pins['RESET'][0]
        if loads[q]!=[(io,'I')] or constants.get(pins['CE'][0])!=1 or reset in constants:continue
        root=drivers.get(pins['D'][0]);lut=cells.get(root,{})
        if lut.get('type') not in ('LUT1','LUT2','LUT3','LUT4') or kept(lut.get('attributes',{})):continue
        if loads[pins['D'][0]]!=[(ffname,'D')] or pins['D'][0] in protected:continue
        width=int(lut['type'][-1]);ins=[lut['connections']['I'+str(i)][0] for i in range(width)];word=int(lut['parameters']['INIT'],2)
        boundary=list(dict.fromkeys(b for b in ins+[reset] if b not in constants));boundary.remove(reset);boundary.append(reset)
        if len(boundary)>4:continue
        truth=0
        for a in range(1<<len(boundary)):
            values={**constants,**{b:(a>>i)&1 for i,b in enumerate(boundary)}};old_d=(word>>sum(values[b]<<i for i,b in enumerate(ins)))&1
            truth|=(old_d & (1-values[reset]))<<a
        padding=4-len(boundary);physical_inputs=[grounds[0]]*padding+boundary;physical_truth=sum(((truth>>(a>>padding))&1)<<a for a in range(16))
        lut.update(type='LUT4',parameters={'INIT':format(physical_truth,'016b')},port_directions={**{f'I{i}':'input' for i in range(4)},'F':'output'},connections={**{f'I{i}':[b] for i,b in enumerate(physical_inputs)},'F':pins['D'].copy()})
        pins['RESET']=[grounds[0]]
        # Independent next-state evaluation of actual old/new primitive pins.
        for a in range(1<<len(boundary)):
            values={**constants,**{b:(a>>i)&1 for i,b in enumerate(boundary)}}
            before=0 if values[reset] else (word>>sum(values[b]<<i for i,b in enumerate(ins)))&1
            after=(physical_truth>>sum(values[b]<<i for i,b in enumerate(physical_inputs)))&1
            if before!=after:raise ValueError('Synchronous next-state relation failed')
        if any(original[ffname].get(k)!=ff.get(k) for k in ('type','parameters','attributes','port_directions')) or any(original[ffname]['connections'][k]!=pins[k] for k in ('CLK','CE','D','Q')):raise ValueError('State/control contract changed beyond reset absorption')
        changes.append(dict(ff=ffname,lut=root,port=location['port'],old_reset=reset,new_reset=grounds[0],boundary=boundary,truth=format(physical_truth,'04x'),checked_assignments=1<<len(boundary),state_count_changed=False,initialization_unchanged=True,latency_unchanged=True))
    return new,changes


def main():
    p=argparse.ArgumentParser();p.add_argument('--base',type=Path,required=True);p.add_argument('--out',type=Path,required=True);a=p.parse_args();reserve(ROOT,50000000);start=time.monotonic();old=load_json(a.base/'packed.json');placed=load_json(a.base/'placement.json');new,changes=transform(old,placed)
    if not changes:raise ValueError('No eligible synchronous-control/data co-mapping')
    a.out=a.out.resolve()
    a.out.mkdir(exist_ok=False);proof=a.out/'sdk_proof';proof.mkdir();models,adapters=formal_library();(proof/'models.v').write_text(models)
    state_names=sorted(n for n,c in old['modules']['top']['cells'].items() if c['type'] in ('DFFRE','DFFSE','DFFCE','DFFPE'))
    for name,design in [('gold',old),('gate',new)]:
        if any(c['type'] not in adapters for c in design['modules']['top']['cells'].values()):raise ValueError('Whole-SDK proof lacks a primitive model')
        view=copy.deepcopy(design);module=view['modules']['top']
        for i,ff in enumerate(state_names):
            port='nextgowin_proof_state_'+str(i)
            if port in module['ports']:raise ValueError('Proof observer port collision')
            module['ports'][port]=dict(direction='output',bits=module['cells'][ff]['connections']['Q'].copy())
        text,_=emit(view);(proof/(name+'.v')).write_text(text)
    commands=proof_commands(proof/'gold.v',proof/'gate.v',proof/'models.v','top',proof)
    # Match architectural state/output observers, not intentionally changed
    # data-LUT or control wires. Original-pair initial SAT remains unchanged.
    commands=[item for cmd in commands for item in (['rename -hide',cmd] if cmd in ('rename top gold','rename top gate') else [cmd])]
    invocation=run(commands,proof,'proof',180)
    if 'Equivalence successfully proven!' not in (proof/'proof.log').read_text() or not re.search(r'Found [1-9]\d* \$equiv cells in equiv:',(proof/'proof.log').read_text()):raise ValueError('Missing nonvacuous sequential proof')
    (a.out/'packed.json').write_text(json.dumps(new,indent=2));(a.out/'placement.json').write_text(json.dumps(placed,indent=2))
    m=load_json(a.base/'manifest.json');m.update(status='placed',netlist_sha256=sha(a.out/'packed.json'),placement_sha256=sha(a.out/'placement.json'),source_netlist_sha256=sha(a.base/'packed.json'),source_placement_sha256=sha(a.base/'placement.json'),sync_reset_unfolding=changes,sdk_sequential_proof=invocation,proof_state_observers=state_names,proof_internal_names_hidden=True,sdk_proof_inputs={f.name:sha(f) for f in proof.iterdir() if f.is_file()},physical_locations_unchanged=True,timing_signoff=False,wall_seconds=time.monotonic()-start)
    (a.out/'manifest.json').write_text(json.dumps(m,indent=2));print('verified synchronous reset absorption',len(changes))


if __name__=='__main__':main()
