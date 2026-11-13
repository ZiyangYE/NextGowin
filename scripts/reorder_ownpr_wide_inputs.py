"""Reorder a Shannon wide LUT cut, preserving geometry and Boolean behavior.

Promote an explicitly selected late signal to a dedicated MUX select level.
Only canonical isolated LUT5..7 trees are supported; final routed STA decides
whether the shorter cell path outweighs changed routing and other inputs.
"""
import argparse,copy,json,shutil
from pathlib import Path
from ownpr_mux_macros import tree,validate
from ownpr_device import Device
from ownpr_place import sha
from native_boundary_cec import prove


def reorder(design,root,late_bit,target_index=None):
    spec=tree(design,root);cells=design['modules']['top']['cells'];width=spec['level']
    constants={'0':0,'1':1}
    for c in cells.values():
        if c['type'] in ('GND','VCC'):constants[next(iter(c['connections'].values()))[0]]=int(c['type']=='VCC')
    leaves=list(spec['leaves']);pins=[cells[n]['connections']['I'+str(i)][0] for n in leaves for i in range(4)]
    old=list(dict.fromkeys(b for b in pins if b not in constants));pruned_support=any(b in constants for b in pins)
    if len(old)!=4:raise ValueError('Wide input reorder requires four nonconstant leaf-support signals; reduced cuts need resynthesis')
    for level in range(5,width+1):
        bits={cells[n]['connections']['S0'][0] for n,x in spec['muxes'].items() if x['level']==level}
        if len(bits)!=1:raise ValueError('Wide input reorder requires one selector per level')
        old.append(next(iter(bits)))
    if len(set(old))!=width or any(not isinstance(b,int) or b in constants for b in old):raise ValueError('Wide input cut must have distinct nonconstant signals')
    if late_bit not in old:raise ValueError('Selected signal is not a wide-cut input')
    target=width-1 if target_index is None else target_index
    if type(target) is not int or not 4<=target<width:raise ValueError('Target must be an existing dedicated selector index')
    if late_bit==old[target]:raise ValueError('Selected signal already drives the requested selector')
    order=list(old);index=order.index(late_bit);order[index],order[target]=order[target],order[index]
    # Evaluate the actual hierarchy, not a remembered pre-transformation INIT.
    drivers={c['connections'][p][0]:n for n,c in cells.items() for p in c['connections'] if c['port_directions'][p]=='output'}
    def evaluate(name,values):
        c=cells[name]
        if c['type']=='LUT4':
            address=sum(values[c['connections']['I'+str(i)][0]]<<i for i in range(4))
            return (int(c['parameters']['INIT'],2)>>address)&1
        select=values[c['connections']['S0'][0]]
        return evaluate(drivers[c['connections']['I'+str(select)][0]],values)
    truth=sum(evaluate(root,{**constants,**{b:(a>>i)&1 for i,b in enumerate(order)}})<<a for a in range(1<<width))
    result=copy.deepcopy(design);changed=result['modules']['top']['cells']
    for select,name in enumerate(leaves):
        c=changed[name]
        c['parameters']['INIT']=format((truth>>(select*16))&0xffff,'016b')
        for i,b in enumerate(order[:4]):c['connections']['I'+str(i)]=[b]
    for name,x in spec['muxes'].items():changed[name]['connections']['S0']=[order[x['level']-1]]
    pruned=[]
    if pruned_support:
        from prune_ownpr_lut_inputs import prune
        result,pruned=prune(result,only_cells=set(leaves))
    return result,dict(root=root,width=width,old_order=old,new_order=order,promoted_signal=late_bit,target_index=target,target_level=target+1,
        truth_hex=hex(truth),cells_and_output_bits_preserved=True,physical_positions_preserved=True,timing_signoff=False,reapplied_unused_input_pruning=len(pruned))


def main():
    p=argparse.ArgumentParser();p.add_argument('--base',type=Path,required=True);p.add_argument('--root',required=True)
    p.add_argument('--late-bit',type=int,required=True);p.add_argument('--target-index',type=int);p.add_argument('--out',type=Path,required=True);a=p.parse_args()
    before=json.loads((a.base/'packed.json').read_text());after,change=reorder(before,a.root,a.late_bit,a.target_index)
    placed=json.loads((a.base/'placement.json').read_text());check=validate(after,placed,Device())
    a.out.mkdir(exist_ok=False);proof=prove(before,after,a.out/'cec',120)
    if not proof or not all(r['cec']['equivalent'] for r in proof):raise ValueError('Wide input reorder lacks a complete equivalence proof')
    (a.out/'packed.json').write_text(json.dumps(after,indent=2));shutil.copyfile(a.base/'placement.json',a.out/'placement.json')
    meta=json.loads((a.base/'manifest.json').read_text());meta.update(status='placed',netlist_sha256=sha(a.out/'packed.json'),placement_sha256=sha(a.out/'placement.json'),
        source_netlist_sha256=sha(a.base/'packed.json'),proof=proof,wide_input_reorder=change,wide_macro_audit=check,timing_signoff=False)
    (a.out/'manifest.json').write_text(json.dumps(meta,indent=2));print(json.dumps(change,indent=2))


if __name__=='__main__':main()
