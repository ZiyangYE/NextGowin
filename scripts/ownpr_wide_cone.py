"""Compose a two-LUT cut and Shannon-expand it into LUT4 + dedicated muxes.

This is a logical transformation and CEC experiment. No physical placement,
internal mux connectivity or timing claim is made by this module.
"""
import argparse,copy,json
from pathlib import Path
from ownpr_lut_lifting import independent
from native_boundary_cec import prove
from ownpr_native_netlist import emit
from ownpr_place import sha


def replace_pair(design,parent,root,top='top'):
    result=copy.deepcopy(design);module=result['modules'][top];cells=module['cells'];a=cells[parent];b=cells[root]
    if a['type'] not in ('LUT1','LUT2','LUT3','LUT4') or b['type'] not in ('LUT1','LUT2','LUT3','LUT4'):raise ValueError('Wide cut requires two ordinary LUTs')
    constants={'0':0,'1':1}
    for c in cells.values():
        if c['type'] in ('GND','VCC'):constants[next(iter(c['connections'].values()))[0]]=int(c['type']=='VCC')
    def inputs(c):return [c['connections']['I'+str(i)][0] for i in range(int(c['type'][-1]))]
    def support(c):return {bit for i,bit in enumerate(inputs(c)) if bit not in constants and not independent(c,i)}
    intermediate=a['connections']['F'][0]
    if intermediate not in support(b):raise ValueError('LUT pair is not a dependent logic cone')
    boundary=sorted((support(b)-{intermediate})|support(a))
    if not 5<=len(boundary)<=7:raise ValueError('Initial dedicated wide cut requires 5..7 independent inputs')
    def evaluate(c,values):
        address=sum(values.get(bit,constants.get(bit,0))<<i for i,bit in enumerate(inputs(c)))
        return (int(c['parameters']['INIT'],2)>>address)&1
    truth=0
    for index in range(1<<len(boundary)):
        values={bit:(index>>i)&1 for i,bit in enumerate(boundary)};values[intermediate]=evaluate(a,values);truth|=evaluate(b,values)<<index
    bits=[bit for c in cells.values() for bs in c['connections'].values() for bit in bs if isinstance(bit,int)];next_bit=max(bits)+1;added=[]
    def allocate():
        nonlocal next_bit
        bit=next_bit;next_bit+=1;return bit
    def add(name,typ,pins,parameters=None):
        if name in cells:raise ValueError('Wide-cut cell name collision')
        cells[name]=dict(type=typ,parameters=parameters or {},attributes={},port_directions={p:'output' if p in ('F','O') else 'input' for p in pins},connections={p:[bit] for p,bit in pins.items()});added.append(name)
    leaves=[]
    for select in range(1<<(len(boundary)-4)):
        bit=allocate();name=f'own_wide_{root}_leaf_{select}';word=(truth>>(select*16))&0xffff
        add(name,'LUT4',{**{f'I{i}':boundary[i] for i in range(4)},'F':bit},{'INIT':format(word,'016b')});leaves.append(bit)
    levels=[];current=leaves
    for level in range(5,len(boundary)+1):
        following=[]
        for i in range(len(current)//2):
            final=level==len(boundary);bit=b['connections']['F'][0] if final else allocate();name=root if final else f'own_wide_{root}_mux_{level}_{i}'
            if final:del cells[root]
            add(name,'MUX2_LUT'+str(level),dict(I0=current[2*i],I1=current[2*i+1],S0=boundary[level-1],O=bit));following.append(bit)
            levels.append(dict(instance=name,level=level,index=i))
        current=following
    return result,dict(parent_preserved=parent,replaced_root=root,cut_bits=boundary,cut_width=len(boundary),truth_hex=hex(truth),leaves=leaves,muxes=levels,added_cells=added,
        placement_implemented=False,physical_modes_verified=False,timing_signoff=False)


def main():
    p=argparse.ArgumentParser();p.add_argument('--netlist',type=Path,required=True);p.add_argument('--parent',required=True);p.add_argument('--root',required=True);p.add_argument('--out',type=Path,required=True);a=p.parse_args();a.out.mkdir(exist_ok=False)
    before=json.loads(a.netlist.read_text());after,info=replace_pair(before,a.parent,a.root);proof=prove(before,after,a.out/'cec',120)
    (a.out/'packed.json').write_text(json.dumps(after,indent=2));text,mapping=emit(after);(a.out/'probe.vg').write_text(text);(a.out/'name_map.json').write_text(json.dumps(mapping,indent=2))
    result=dict(status='logic_equivalent_only',source_sha256=sha(a.netlist),netlist_sha256=sha(a.out/'packed.json'),emitted_sha256=sha(a.out/'probe.vg'),transform=info,proof=proof,
        vendor_pnr_executed=False,open_chip_database_used=False,timing_signoff=False)
    (a.out/'manifest.json').write_text(json.dumps(result,indent=2));print(json.dumps({k:v for k,v in result.items() if k!='proof'},indent=2))


if __name__=='__main__':main()
