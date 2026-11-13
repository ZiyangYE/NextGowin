"""Realize a small LUT function using calibrated LUT5/6/7 selector stages.

Shannon cofactors occupy LUT4 leaves under the existing physical macro rules.
One selector costs one extra LUT; up to three selectors can use constant leaves.
Routing and final STA must establish any gain, including competing inputs.
"""
import argparse,copy,json
from pathlib import Path
from register_control_map import normalize
from native_boundary_cec import prove
from ownpr_place import sha


def replace(design,root,late_bit=None,selector_count=None):
    result=copy.deepcopy(design);module=result['modules']['top'];cells=module['cells'];cell=cells[root]
    if cell['type'] not in ('LUT1','LUT2','LUT3','LUT4'):raise ValueError('Selector cascade requires an ordinary LUT')
    constants={};ground=None
    for c in cells.values():
        if c['type'] in ('GND','VCC'):
            bit=next(iter(c['connections'].values()))[0];constants[bit]='1' if c['type']=='VCC' else '0'
            if c['type']=='GND':ground=bit
    inputs=[constants.get(cell['connections']['I'+str(i)][0],cell['connections']['I'+str(i)][0]) for i in range(int(cell['type'][-1]))]
    support,truth=normalize(inputs,int(cell['parameters']['INIT'],2));support=list(support)
    if not 1<=len(support)<=4:raise ValueError('Selector cascade requires 1..4 independent nonconstant inputs')
    stages=len(support) if selector_count is None else selector_count
    if not 1<=stages<=min(3,len(support)):raise ValueError('Require 1..3 selector stages within the function support')
    if late_bit is not None and late_bit not in support:raise ValueError('Late bit is not in the actual function support')
    order=[b for b in support if b!=late_bit]+([late_bit] if late_bit is not None else [])
    if late_bit is None:order=list(support)
    data_order=order[:-stages];selectors=order[-stages:]
    word=0
    for assignment in range(1<<len(order)):
        values={b:assignment>>i&1 for i,b in enumerate(order)}
        original=sum(values[b]<<i for i,b in enumerate(support));word|=(truth>>original&1)<<assignment
    bits=[b for c in cells.values() for bs in c['connections'].values() for b in bs if isinstance(b,int)]
    bits.extend(b for kind in ('ports','netnames') for r in module.get(kind,{}).values() for b in r['bits'] if isinstance(b,int))
    next_bit=max(bits,default=1)+1;added=[]
    def allocate():
        nonlocal next_bit
        b=next_bit;next_bit+=1;return b
    def add(name,typ,pins,params=None):
        if name in cells:raise ValueError('Selector cascade name collision')
        cells[name]=dict(type=typ,parameters=params or {},attributes={},port_directions={p:'output' if p in ('F','O','G') else 'input' for p in pins},connections={p:[b] for p,b in pins.items()});added.append(name)
    if ground is None:
        ground=allocate();add('own_selector_'+root+'_ground','GND',{'G':ground})
    output=cell['connections']['F'][0];current=[];leaves=[]
    for index in range(1<<stages):
        bit=allocate();name=f'own_selector_{root}_leaf_{index}'
        init=sum(((word>>((index<<len(data_order))|(v&((1<<len(data_order))-1))))&1)<<v for v in range(16))
        add(name,'LUT4',{**{f'I{i}':data_order[i] if i<len(data_order) else ground for i in range(4)},'F':bit},{'INIT':format(init,'016b')})
        current.append(bit);leaves.append(name)
    muxes=[]
    for level in range(5,5+stages):
        following=[]
        for i in range(len(current)//2):
            final=level==4+stages;bit=output if final else allocate();name=root if final else f'own_selector_{root}_mux_{level}_{i}'
            if final:del cells[root]
            add(name,'MUX2_LUT'+str(level),dict(I0=current[2*i],I1=current[2*i+1],S0=selectors[level-5],O=bit));following.append(bit);muxes.append(name)
        current=following
    return result,dict(replaced_root=root,original_primitive=cell['type'],support=support,selector_order=selectors,data_order=data_order,truth_hex=hex(word),
        cut_width=4+stages,constant_leaf_count=sum(cells[n]['parameters']['INIT'] in ('0'*16,'1'*16) for n in leaves),lut_growth=len(leaves)-1,mux_count=len(muxes),added_cells=added,
        logical_cycles_preserved=True,timing_signoff=False,scope='Logical selector realization; legal placement, loaded routing and final STA still required')


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--netlist',type=Path,required=True);p.add_argument('--root',required=True);p.add_argument('--late-bit',type=int);p.add_argument('--selector-count',type=int,choices=(1,2,3));p.add_argument('--out',type=Path,required=True);a=p.parse_args()
    from workspace_budget import reserve
    from ownpr_device import ROOT
    reserve(ROOT,10_000_000);before=json.loads(a.netlist.read_text());after,change=replace(before,a.root,a.late_bit,a.selector_count)
    a.out.mkdir(exist_ok=False);proof=prove(before,after,a.out/'cec',120)
    if not proof or not all(r['cec']['equivalent'] for r in proof):raise ValueError('Selector cascade CEC failed')
    (a.out/'packed.json').write_text(json.dumps(after,indent=2));result=dict(status='logic_equivalent_only',source_sha256=sha(a.netlist),
        netlist_sha256=sha(a.out/'packed.json'),transform=change,proof=proof,vendor_pnr_executed=False,open_chip_database_used=False,timing_signoff=False)
    (a.out/'manifest.json').write_text(json.dumps(result,indent=2));print(json.dumps(change,indent=2))


if __name__=='__main__':main()
