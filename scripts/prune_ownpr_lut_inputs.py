"""Tie functionally irrelevant LUT inputs without changing INIT or placement."""
import argparse,copy,json
from pathlib import Path
from ownpr_place import sha
from native_boundary_cec import prove


def prune(design,only_cells=None):
    result=copy.deepcopy(design);cells=result['modules']['top']['cells'];changes=[]
    constants={'0','1'};ground=None
    for cell in cells.values():
        if cell['type'] in ('GND','VCC'):
            bit=next(iter(cell['connections'].values()))[0];constants.add(bit)
            if cell['type']=='GND':ground=bit
    unused=[]
    for name,cell in cells.items():
        if only_cells is not None and name not in only_cells:continue
        if cell['type'] not in ('LUT1','LUT2','LUT3','LUT4'):continue
        width=int(cell['type'][-1]);truth=int(cell['parameters']['INIT'],2)
        for index in range(width):
            pin='I'+str(index);bit=cell['connections'][pin][0]
            if bit in constants:continue
            if all((truth>>a&1)==(truth>>(a^(1<<index))&1) for a in range(1<<width)):
                unused.append((name,pin,bit))
    if not unused:return result,[]
    if ground is None:
        ground=max((b for c in cells.values() for bits in c['connections'].values() for b in bits if isinstance(b,int)),default=1)+1
        name='own_unused_lut_ground'
        while name in cells:name+='_' 
        cells[name]=dict(type='GND',parameters={},attributes={},connections={'G':[ground]},port_directions={'G':'output'})
    for name,pin,bit in unused:
        cells[name]['connections'][pin]=[ground]
        changes.append(dict(instance=name,pin=pin,old_bit=bit,constant_bit=ground,constant_value=0))
    return result,changes


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--base',type=Path,required=True);p.add_argument('--out',type=Path,required=True);a=p.parse_args()
    from ownpr_device import ROOT,Device
    from ownpr_mux_macros import validate
    from workspace_budget import reserve
    reserve(ROOT,10_000_000);before=json.loads((a.base/'packed.json').read_text());after,changes=prune(before)
    if not changes:raise ValueError('No connected functionally unused LUT inputs')
    placed=json.loads((a.base/'placement.json').read_text());validate(after,placed,Device())
    a.out.mkdir(exist_ok=False);proof=prove(before,after,a.out/'cec',120)
    if not proof or not all(r['cec']['equivalent'] for r in proof):raise ValueError('LUT-input pruning CEC failed')
    (a.out/'packed.json').write_text(json.dumps(after,indent=2));(a.out/'placement.json').write_bytes((a.base/'placement.json').read_bytes())
    meta=json.loads((a.base/'manifest.json').read_text());meta.update(status='placed',proof=proof,
        netlist_sha256=sha(a.out/'packed.json'),placement_sha256=sha(a.out/'placement.json'),
        source_netlist_sha256=sha(a.base/'packed.json'),unused_input_pruning=changes,timing_signoff=False)
    (a.out/'manifest.json').write_text(json.dumps(meta,indent=2));print(json.dumps(dict(pruned_inputs=len(changes),luts=len({r['instance'] for r in changes}))))


if __name__=='__main__':main()
