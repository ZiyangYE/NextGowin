"""Capacity and fixed-link checks for isolated, single-CFU wide LUT trees."""
from collections import defaultdict
from ownpr_mux_architecture import children


def tree(design,root,root_slot=None):
    cells=design['modules']['top']['cells'];drivers={};loads=defaultdict(list)
    for name,c in cells.items():
        for pin,bits in c['connections'].items():
            for bit in bits:
                if c['port_directions'][pin]=='output':
                    if bit in drivers:raise ValueError('Multiple drivers in wide mux cone')
                    drivers[bit]=(name,pin)
                else:loads[bit].append((name,pin))
    level=int(cells[root]['type'][-1]);root_slot={5:0,6:1,7:3}.get(level) if root_slot is None else root_slot
    if root_slot is None:raise ValueError('Cross-CFU LUT8 macro is not implemented')
    muxes={};leaves={};links=[];seen=set()
    def visit(name,level,slot):
        if name in seen:raise ValueError('Shared or cyclic dedicated mux tree')
        seen.add(name);cell=cells[name]
        if cell['type']!='MUX2_LUT'+str(level):raise ValueError('Wrong dedicated mux hierarchy')
        muxes[name]=dict(level=level,slot=slot)
        for pin,(typ,child_slot) in zip(('I0','I1'),children(level,slot)):
            bit=cell['connections'][pin][0]
            if bit not in drivers:raise ValueError('Undriven dedicated mux input')
            source,out=drivers[bit]
            if cells[source]['type']!=typ or out!=('F' if typ=='LUT4' else 'O') or loads[bit]!=[(name,pin)]:raise ValueError('Dedicated mux input must have a private matching driver')
            links.append(dict(source=source,source_pin=out,destination=name,pin=pin,source_slot=child_slot,destination_slot=slot))
            if typ=='LUT4':
                if source in seen:raise ValueError('Shared wide LUT leaf')
                seen.add(source);leaves[source]=child_slot
            else:visit(source,level-1,child_slot)
    visit(root,level,root_slot)
    return dict(root=root,level=level,muxes=muxes,leaves=leaves,links=links)


def validate(design,placed,device):
    from ownpr_wide_ff_packing import pairs
    wide_pairs=pairs(design,placed,device);paired_leaves={r['leaf'] for r in wide_pairs}
    cells=design['modules']['top']['cells'];muxes=placed.get('mux_clusters',{});expected={n for n,c in cells.items() if c['type'].startswith('MUX2_LUT')}
    if set(muxes)!=expected:raise ValueError('Missing or extra dedicated mux placement')
    occupied=set();covered=set();links=[];macros=[]
    for macro in placed.get('wide_macros',[]):
        root=macro['root'];r,c=macro['row'],macro['column'];spec=tree(design,root,macro['root_slot']);leaf_names=set(spec['leaves'])
        if not (0<=r<len(device.grid) and 0<=c<len(device.grid[r])):raise ValueError('Wide-mux CFU is outside the device')
        if (r,c) in occupied or device.grid[r][c]!=17:raise ValueError('Conflicting or uncalibrated wide-mux CFU')
        occupied.add((r,c))
        for name,row in spec['muxes'].items():
            if name in covered or muxes[name]!={**row,'row':r,'column':c}:raise ValueError('Mux hierarchy changed physical slots')
            covered.add(name)
        for name,slot in spec['leaves'].items():
            row=placed['clusters'].get(name)
            if row is None or (row['row'],row['column'],row['slot'])!=(r,c,slot) or row['ff'] and name not in paired_leaves:raise ValueError('Wide leaf is misplaced or shares an uncalibrated FF')
        extras={name:row for name,row in placed['clusters'].items() if (row['row'],row['column'])==(r,c) and name not in leaf_names}
        shared_evidence=None
        if extras or macro.get('ordinary_sharing'):
            from ownpr_wide_lut_sharing import check
            shared_evidence=check(design,placed,device,macro,spec,extras)
        if any((row['row'],row['column'])==(r,c) for row in placed.get('arithmetic_clusters',{}).values()):raise ValueError('Arithmetic logic shares a wide-mux CFU')
        for link in spec['links']:links.append(dict(link,row=r,column=c))
        macros.append(dict(root=root,level=spec['level'],row=r,column=c,leaves=len(leaf_names),muxes=len(spec['muxes']),ordinary_luts=sorted(extras),sharing_calibration_sha256=shared_evidence))
    if covered!=set(muxes):raise ValueError('Dedicated mux macro coverage is incomplete')
    return dict(status='wide_mux_capacity_and_pattern_checked',macros=macros,links=links,capacity_verified=True,shared_ff_alu_supported=False,full_physical_legality_verified=False,wide_ff_pairs=wide_pairs)
