"""Bounded K-feasible cut enumeration across LUT and dedicated-mux logic.

All old interior cells remain for other loads. Replacing one output function
does not alter state or cycles. Enumerated cuts and geometry are proposals;
full boundary CEC and physical STA are required for acceptance.
"""
import copy
from register_control_map import logic,normalize


def wide_absorption_boundaries(design,model,root):
    """Preserve complete wide trees when enumerating their downstream fusion."""
    from ownpr_mux_macros import tree
    cells=model.cells;result=set()
    for bit in model.functions[root][0]:
        parent=model.drivers.get(bit)
        if parent is None or cells[parent]['type'] not in ('MUX2_LUT5','MUX2_LUT6'):continue
        spec=tree(design,parent);members=set(spec['leaves'])|set(spec['muxes'])|{root}
        internal={b for n in members for p,bs in cells[n]['connections'].items() if cells[n]['port_directions'][p]=='output' for b in bs}
        boundary=frozenset(b for n in members for p,bs in cells[n]['connections'].items() if cells[n]['port_directions'][p]=='input' for b in bs if b not in internal and b not in model.constants)
        if 1<=len(boundary)<=model.k:result.add(boundary)
    return tuple(sorted(result,key=lambda b:(len(b),tuple(sorted(b)))))

class Cuts:
    def __init__(self,design,k=6,depth=6,limit=96):
        if not 1<=k<=7 or not 1<=depth<=10 or not 1<=limit<=512:raise ValueError('Invalid bounded cut limits')
        self.design=design;self.cells=design['modules']['top']['cells'];self.k=k;self.depth=depth;self.limit=limit
        self.constants={'0':'0','1':'1'};self.drivers={};self.functions={};self.cache={};self.active=set()
        for n,c in self.cells.items():
            for p,bs in c['connections'].items():
                if c['port_directions'][p]=='output':
                    for b in bs:
                        if b in self.drivers:raise ValueError('Multiple cut drivers')
                        self.drivers[b]=n
            if c['type'] in ('VCC','GND'):self.constants[next(iter(c['connections'].values()))[0]]='1' if c['type']=='VCC' else '0'
        for n,c in self.cells.items():
            f=logic(c)
            if not f:continue
            if c['type'].startswith('LUT'):
                width=int(c['type'][-1]);params=c.get('parameters',{});word=params.get('INIT','')
                if set(params)!={'INIT'} or len(word)!=(1<<width) or set(word)-{'0','1'}:raise ValueError('Unsupported LUT parameter contract')
            elif c.get('parameters',{}):raise ValueError('Unsupported dedicated-mux parameters')
            ins,out,truth=f;support,word=normalize([self.constants.get(b,b) for b in ins],truth)
            self.functions[n]=(tuple(support),out,word)

    def enumerate(self,bit,depth=None):
        depth=self.depth if depth is None else depth
        if bit in self.constants:return (frozenset(),)
        key=(bit,depth)
        if key in self.cache:return self.cache[key]
        if bit in self.active:raise ValueError('Combinational cycle in cut graph')
        name=self.drivers.get(bit);trivial=frozenset([bit])
        if depth==0 or name not in self.functions:return (trivial,)
        self.active.add(bit)
        try:
            combined={frozenset()}
            for child in self.functions[name][0]:
                choices=self.enumerate(child,depth-1)
                combined={a|b for a in combined for b in choices if len(a|b)<=self.k}
                # Deterministic bound limits enumeration, not a completeness claim.
                combined=set(sorted(combined,key=lambda c:(len(c),tuple(sorted(c))))[:self.limit])
            result=tuple([trivial]+[c for c in sorted(combined,key=lambda c:(len(c),tuple(sorted(c)))) if c!=trivial][:self.limit])
            self.cache[key]=result;return result
        finally:self.active.remove(bit)

    def function(self,root,boundary):
        if root not in self.functions:raise ValueError('Root is not mapped combinational logic')
        boundary=tuple(sorted(boundary));out=self.functions[root][1]
        if not 1<=len(boundary)<=self.k or out in boundary:raise ValueError('Trivial or invalid cut')
        inside=set();active=set()
        def evaluate(bit,values):
            if bit in values:return values[bit]
            if bit in self.constants:return int(self.constants[bit])
            name=self.drivers.get(bit)
            if name not in self.functions:raise ValueError('Cut fails to cover a state/input boundary')
            if name in active:raise ValueError('Combinational cycle inside cut')
            active.add(name);ins,_,truth=self.functions[name];inside.add(name)
            address=sum(evaluate(b,values)<<i for i,b in enumerate(ins));value=truth>>address&1
            active.remove(name);values[bit]=value;return value
        truth=0
        for a in range(1<<len(boundary)):
            values={b:a>>i&1 for i,b in enumerate(boundary)};truth|=evaluate(out,values)<<a
        support,word=normalize(boundary,truth)
        return dict(root=root,evaluation_boundary=list(boundary),cut_bits=list(support),cut_width=len(support),truth_hex=hex(word),interior=sorted(inside),interior_count=len(inside))

def replace(design,cut,late_bit=None):
    # Recompute the function from the input graph; do not trust a saved truth table.
    verified=Cuts(design,k=max(6,len(cut['evaluation_boundary']))).function(cut['root'],cut['evaluation_boundary'])
    if verified!=cut:raise ValueError('Cut certificate changed')
    boundary=list(cut['cut_bits']);k=len(boundary)
    if not 1<=k<=7:raise ValueError('Unsupported constant or large cut')
    private={b for c in design['modules']['top']['cells'].values() if c['type'].startswith('MUX2_LUT') for pin in ('I0','I1') for b in c['connections'][pin]}
    if set(boundary)&private:raise ValueError('Cut boundary taps a private dedicated-mux link')
    old_truth=int(cut['truth_hex'],16)
    if late_bit is not None:
        if late_bit not in boundary:raise ValueError('Late signal outside cut')
        boundary.remove(late_bit);boundary.append(late_bit)
    word=0
    for a in range(1<<k):
        values={b:a>>i&1 for i,b in enumerate(boundary)};old_address=sum(values[b]<<i for i,b in enumerate(cut['cut_bits']))
        word|=(old_truth>>old_address&1)<<a
    after=copy.deepcopy(design);module=after['modules']['top'];cells=module['cells'];root=cut['root'];old=cells[root]
    if old['type'] not in ('LUT1','LUT2','LUT3','LUT4'):raise ValueError('Physical root must be an ordinary LUT')
    output=old['connections']['F'][0];attributes=copy.deepcopy(old.get('attributes',{}));added=[]
    bits=[b for c in cells.values() for bs in c['connections'].values() for b in bs]
    bits.extend(b for r in module.get('netnames',{}).values() for b in r['bits']);bits.extend(b for r in module['ports'].values() for b in r['bits'])
    next_bit=1+max(b for b in bits if isinstance(b,int))
    def allocate():
        nonlocal next_bit
        result=next_bit;next_bit+=1;return result
    def add(name,typ,pins,truth=None):
        if name in cells and name!=root:raise ValueError('Cut-generated cell name collision')
        cells[name]=dict(type=typ,parameters={} if truth is None else {'INIT':truth},attributes=attributes if name==root else {},
            port_directions={p:'output' if p in ('F','O') else 'input' for p in pins},connections={p:[b] for p,b in pins.items()})
        if name!=root:added.append(name)
    if k<=4:
        add(root,'LUT'+str(k),{**{f'I{i}':b for i,b in enumerate(boundary)},'F':output},format(word,f'0{1<<k}b'))
    else:
        current=[]
        for select in range(1<<(k-4)):
            bit=allocate();name=f'ng_multicut_{root}_leaf_{select}'
            add(name,'LUT4',{**{f'I{i}':boundary[i] for i in range(4)},'F':bit},format(word>>(16*select)&65535,'016b'));current.append(bit)
        for level in range(5,k+1):
            following=[]
            for i in range(len(current)//2):
                final=level==k;bit=output if final else allocate();name=root if final else f'ng_multicut_{root}_mux_{level}_{i}'
                add(name,'MUX2_LUT'+str(level),dict(I0=current[2*i],I1=current[2*i+1],S0=boundary[level-1],O=bit));following.append(bit)
            current=following
    return after,dict(**cut,ordered_boundary=boundary,late_bit=late_bit,added_cells=added,lut_growth=max(0,(1<<max(0,k-4))-1),
        original_interior_preserved=True,interface_cycles_changed=False,physical_gain_unproven=True)
