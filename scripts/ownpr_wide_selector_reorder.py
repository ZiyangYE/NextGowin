"""Re-cofactor a private LUT5/6 tree while preserving cells, nets and placement.

Promoting a boundary signal to the root selector is only a timing hypothesis.
The caller must prove full boundary equivalence and reroute changed input nets.
"""
import copy
from ownpr_mux_macros import tree
from ownpr_multilevel_cut import Cuts


def transform(design,root,late_bit):
    cells=design['modules']['top']['cells'];spec=tree(design,root);level=spec['level']
    if level not in (5,6):raise ValueError('Only single-CFU LUT5/6 reorder is supported')
    members=set(spec['leaves'])|set(spec['muxes'])
    outputs={b for n in members for p,bs in cells[n]['connections'].items() if cells[n]['port_directions'][p]=='output' for b in bs}
    private=outputs-set(cells[root]['connections']['O'])
    module=design['modules']['top']
    if any(private.intersection(p['bits']) for p in module['ports'].values()):raise ValueError('Private mux link is externally observed')
    def kept(attributes):
        value=str(attributes.get('keep','0'))
        return any(ch!='0' for ch in value)
    if any(kept(cells[n].get('attributes',{})) for n in members) or any(private.intersection(n['bits']) and kept(n.get('attributes',{})) for n in module.get('netnames',{}).values()):raise ValueError('Kept internal macro values cannot be rewritten')
    model=Cuts(design,k=6);boundary={b for n in members for p,bs in cells[n]['connections'].items() if cells[n]['port_directions'][p]=='input' for b in bs if b not in outputs and b not in model.constants}
    cut=model.function(root,boundary)
    if cut['cut_width']!=level or set(cut['interior'])!=members:raise ValueError('Macro is not a private full-support K-LUT')
    if late_bit not in cut['cut_bits']:raise ValueError('Promoted bit is not in macro support')
    if cells[root]['connections']['S0']==[late_bit]:raise ValueError('Signal is already the root selector')
    order=[b for b in cut['cut_bits'] if b!=late_bit]+[late_bit];original=int(cut['truth_hex'],16);word=0
    for address in range(1<<level):
        values={b:(address>>i)&1 for i,b in enumerate(order)}
        old_address=sum(values[b]<<i for i,b in enumerate(cut['cut_bits']))
        word|=((original>>old_address)&1)<<address
    after=copy.deepcopy(design);new=after['modules']['top']['cells'];tied=[]
    zero=next((b for b,v in model.constants.items() if v=='0' and isinstance(b,int)),'0')
    drivers={b:n for n in members for p,bs in cells[n]['connections'].items() if cells[n]['port_directions'][p]=='output' for b in bs}
    def assign(name,lvl,selectors):
        if lvl==4:
            for i in range(4):new[name]['connections']['I'+str(i)]=[order[i]]
            offset=sum(v<<i for i,v in selectors.items())
            truth=sum(((word>>(offset|a))&1)<<a for a in range(16))
            new[name]['parameters']['INIT']=format(truth,'016b')
            for i in range(4):
                if all(((truth>>a)&1)==((truth>>(a^(1<<i)))&1) for a in range(16)):
                    new[name]['connections']['I'+str(i)]=[zero];tied.append([name,'I'+str(i)])
            return
        new[name]['connections']['S0']=[order[lvl-1]]
        for value,pin in enumerate(('I0','I1')):
            assign(drivers[cells[name]['connections'][pin][0]],lvl-1,{**selectors,lvl-1:value})
    assign(root,level,{})
    verified=Cuts(after,k=6).function(root,boundary)
    if verified!=cut:raise ValueError('Exhaustive macro equivalence failed')
    if any(new[n]!=c for n,c in cells.items() if n not in members):raise ValueError('Unrelated cell changed')
    return after,dict(root=root,level=level,promoted_bit=late_bit,old_selector=cells[root]['connections']['S0'][0],ordered_boundary=order,truth_hex=cut['truth_hex'],exhaustive_vectors=1<<level,exhaustive_equivalent=True,unused_leaf_pins_tied_zero=tied,changed_cells=sorted(n for n in members if new[n]!=cells[n]),cell_count_unchanged=True,output_bit_unchanged=True,interface_cycles_changed=False,loaded_timing_gain_unproven=True)
