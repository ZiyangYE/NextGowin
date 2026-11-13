"""Insert root I/O buffers while respecting pads inside preserved IP hierarchy."""
import copy

PAD_PINS = {
    'IBUF': ('I',), 'OBUF': ('O',), 'TBUF': ('O',), 'IOBUF': ('IO',),
    'TLVDS_IBUF': ('I','IB'), 'ELVDS_IBUF': ('I','IB'),
    'TLVDS_OBUF': ('O','OB'), 'ELVDS_OBUF': ('O','OB'),
    'TLVDS_IOBUF': ('IO','IOB'), 'ELVDS_IOBUF': ('IO','IOB'),
    'TLVDS_TBUF': ('O','OB'), 'ELVDS_TBUF': ('O','OB'),
}


def insert(design, top):
    design = copy.deepcopy(design)
    modules = design['modules']
    parent = {}
    def find(x):
        root = x
        while parent.get(root, root) != root:
            root = parent[root]
        while x != root:
            following = parent.get(x, x)
            parent[x] = root
            x = following
        return root
    def union(a,b):
        a,b = find(a),find(b)
        if a != b:
            parent[b] = a
    pads = []
    gsrs = []
    def walk(name, path, stack):
        if name in stack:
            raise ValueError('Recursive hierarchy')
        for cell_name, cell in modules[name].get('cells',{}).items():
            child = cell['type']
            child_path = path+(cell_name,)
            if child == 'GSR':
                gsrs.append(child_path)
            for pin in PAD_PINS.get(child, ()):
                pads.extend((path,b) for b in cell['connections'].get(pin,[]) if isinstance(b,int))
            for pin in modules.get(child,{}).get('attributes',{}).get('ng_pad_ports','').split():
                if pin not in cell['connections']:raise ValueError('Opaque macro I/O pin missing')
                pads.extend((path,b) for b in cell['connections'][pin] if isinstance(b,int))
            if child not in modules or int(modules[child].get('attributes',{}).get('blackbox','0'),2):
                continue
            for port,row in modules[child]['ports'].items():
                connection = cell['connections'].get(port)
                if connection is None and row['direction']=='output':
                    continue
                if connection is None or len(connection) != len(row['bits']):
                    raise ValueError('Hierarchy port connection missing or wrong width: '+str(child_path)+'/'+port)
                for a,b in zip(connection,row['bits']):
                    if isinstance(a,int) and isinstance(b,int):
                        union((path,a),(child_path,b))
            walk(child, child_path, stack+(name,))
    walk(top, (), ())
    padded = {find(p) for p in pads}
    module = modules[top]
    maximum = max((b for row in module['netnames'].values() for b in row['bits'] if isinstance(b,int)),default=1)
    maximum = max(maximum,max((b for cell in module['cells'].values()
        for bits in cell['connections'].values() for b in bits if isinstance(b,int)),default=1))
    maximum = max(maximum,max((b for row in module['ports'].values()
        for b in row['bits'] if isinstance(b,int)),default=1))
    replace = {}
    new_cells = {}
    inserted = []
    retained = []
    for port,row in sorted(module['ports'].items(),key=lambda item:item[1]['direction']!='input'):
        for index,bit in enumerate(row['bits']):
            if not isinstance(bit,int):
                if bit not in ('0','1') or row['direction'] not in ('input','output'):
                    raise ValueError('Unknown or bidirectional constant root port')
                maximum+=1;external=maximum;row['bits'][index]=external
                if port in module['netnames']:module['netnames'][port]['bits'][index]=external
                name='ng_pad_'+str(len(inserted))
                if name in module['cells'] or name in new_cells:raise ValueError('Generated I/O cell name collision')
                if row['direction']=='output':kind='OBUF';inside=bit
                else:
                    kind='IBUF';maximum+=1;inside=maximum
                new_cells[name]=dict(type=kind,hide_name=0,parameters={},attributes={},port_directions=dict(I='input',O='output'),
                                     connections=dict(I=[inside if kind=='OBUF' else external],O=[external if kind=='OBUF' else inside]))
                inserted.append(dict(port=port,index=index,kind=kind,original_constant=bit))
                continue
            if find(((),bit)) in padded:
                retained.append(dict(port=port,index=index))
                continue
            if bit in replace:
                if row['direction']!='output':raise ValueError('Aliased external inputs require explicit I/O policy')
                maximum+=1;external=maximum;row['bits'][index]=external
                if port in module['netnames']:module['netnames'][port]['bits'][index]=external
                name='ng_pad_'+str(len(inserted))
                if name in module['cells'] or name in new_cells:raise ValueError('Generated I/O cell name collision')
                new_cells[name]=dict(type='OBUF',hide_name=0,parameters={},attributes={},port_directions=dict(I='input',O='output'),
                                     connections=dict(I=[replace[bit]],O=[external]))
                inserted.append(dict(port=port,index=index,kind='OBUF',aliased_output=True))
                continue
            if row['direction'] not in ('input','output'):
                raise ValueError('Generic unbuffered inout is unsupported; use a native IOBUF')
            maximum += 1
            replace[bit] = maximum
            name = 'ng_pad_'+str(len(inserted))
            if name in module['cells']:
                raise ValueError('Generated I/O cell name collision')
            is_input = row['direction']=='input'
            new_cells[name] = dict(type='IBUF' if is_input else 'OBUF',hide_name=0,parameters={},attributes={},
                port_directions=dict(I='input',O='output'),
                connections=dict(I=[bit if is_input else maximum], O=[maximum if is_input else bit]))
            inserted.append(dict(port=port,index=index,kind=new_cells[name]['type']))
    for cell in module['cells'].values():
        cell['connections'] = {p:[replace.get(b,b) for b in bits] for p,bits in cell['connections'].items()}
    # Original root port names denote package-side nets; preserve internal init
    # and keep attributes on a separate alias before redirecting other netnames.
    for name,row in list(module['netnames'].items()):
        bits = [replace.get(b,b) for b in row['bits']]
        if name in module['ports']:
            if bits != row['bits']:
                alias = 'ng_internal_'+name
                if alias in module['netnames']:
                    raise ValueError('Internal alias name collision')
                internal = copy.deepcopy(row)
                internal['bits'] = bits
                module['netnames'][alias] = internal
                row['attributes'] = {k:v for k,v in row.get('attributes',{}).items() if k!='init'}
        else:
            row['bits'] = bits
    module['cells'].update(new_cells)
    if not gsrs:
        maximum += 1
        for name in ('ng_gsr_vcc','ng_gsr'):
            if name in module['cells']:
                raise ValueError('Global-reset cell name collision')
        module['cells']['ng_gsr_vcc'] = dict(type='VCC',hide_name=0,attributes={},parameters={},
            port_directions={'V':'output'},connections={'V':[maximum]})
        module['cells']['ng_gsr'] = dict(type='GSR',hide_name=0,attributes={},parameters={},
            port_directions={'GSRI':'input'},connections={'GSRI':[maximum]})
    return design, dict(inserted_pads=inserted, preserved_hierarchical_pads=retained,
                        existing_global_reset_count=len(gsrs), inserted_global_reset=not gsrs)
