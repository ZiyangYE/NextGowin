"""Honor the installed native netlist reader's primitive parameter formats."""
import hashlib
from pathlib import Path
import re
from gowin_cones import literal
from split_primitive_models import units

from gowin_installation import SDK
LIBRARY=SDK/'simlib/gw5a/prim_sim.v'
EXPECTED='7109c2568188627465d218bc099cf72f12e90807c21d4d862d2335b499043544'


def formats():
    data=LIBRARY.read_bytes()
    if hashlib.sha256(data).hexdigest()!=EXPECTED:
        raise ValueError('Vendor primitive source changed; revalidate parameter formats')
    text=data.decode('utf-8')
    result={}
    for name,row in units(text).items():
        body=text[row['start']:row['end']]
        result[name]=dict(re.findall(r'^\s*parameter\s+(\w+)\s*=\s*([^;]+);',body,re.M))
    return result


def integer(text):
    if re.fullmatch(r'\d+',text.strip()):return int(text)
    if re.fullmatch(r"\{\s*0\s*\{\s*1'[bB]0\s*\}\s*\}",text.strip()):return 0
    return literal(text.strip())[1]


def normalize(text):
    library=formats();instances={};current=None;changed=[];lines=[]
    for line in text.splitlines():
        module=re.match(r'^module\s+(\S+)\s*\(',line)
        if module:
            current=module[1];instances={}
        instance=re.match(r'^\s{2}(\S+)\s+(\\[^\s]+|[\w$]+)\s*\(',line)
        if instance:instances[instance[2]]=instance[1]
        param=re.match(r'^(\s*defparam\s+)(.*?)\s*\.(\w+)\s*=\s*([^;]+);\s*$',line)
        if param:
            cell=instances.get(param[2].strip());key=param[3];value=param[4]
            default=library.get(cell,{}).get(key)
            # A zero-width repeat is how write_verilog prints an empty string
            # after a JSON round trip. Unset vendor string properties inherit
            # the native primitive default; they must not become numeric zero.
            if re.fullmatch(r"\{\s*0\s*\{\s*1'[bB]0\s*\}\s*\}",value.strip()) and (default is None or default.strip().startswith('"')):
                changed.append(dict(module=current,instance=param[2].strip(),cell=cell,parameter=key,
                    before=value,after=None,policy='Omit empty/unset vendor string property; use native primitive default'))
                continue
            if default is not None:
                default=default.strip()
                sized=re.fullmatch(r"(\d+)'([bBoOdDhH])[0-9a-fA-FxXzZ_]+",default)
                if sized or default.isdigit():
                    number=integer(value)
                    if sized:
                        width=int(sized[1]);base=sized[2].lower()
                        if number.bit_length()>width:
                            raise ValueError('Primitive parameter exceeds vendor width: '+cell+'.'+key)
                        digits=format(number,'0'+str(width)+'b') if base=='b' else format(number,'0'+str((width+3)//4)+'x') if base=='h' else str(number) if base=='d' else format(number,'o')
                        replacement=str(width)+"'"+base+digits
                    else:replacement=str(number)
                    if integer(replacement)!=number:raise ValueError('Numeric parameter changed')
                    if replacement!=value:
                        changed.append(dict(module=current,instance=param[2].strip(),cell=cell,parameter=key,before=value,after=replacement))
                        line=param[1]+param[2].rstrip()+(' ' if param[2].strip().startswith('\\') else '')+'.'+key+' = '+replacement+';'
        lines.append(line)
    return '\n'.join(lines)+'\n',dict(parameter_format_changes=changed,primitive_library_sha256=EXPECTED)


def complete_output_ports(design):
    """The native parser requires every hierarchical output pin to be named."""
    modules=design['modules'];changes=[]
    for name,module in modules.items():
        if int(module.get('attributes',{}).get('blackbox','0'),2):continue
        bits=[b for row in module.get('netnames',{}).values() for b in row['bits']]
        bits.extend(b for c in module.get('cells',{}).values() for v in c['connections'].values() for b in v)
        maximum=max((b for b in bits if isinstance(b,int)),default=1)
        for instance,cell in module.get('cells',{}).items():
            child=modules.get(cell['type'])
            if child is None:continue
            for port,row in child['ports'].items():
                if cell['connections'].get(port):continue
                if row['direction']!='output':
                    if int(child.get('attributes',{}).get('blackbox','0'),2):continue
                    raise ValueError('Missing hierarchical input port')
                cell['connections'][port]=list(range(maximum+1,maximum+1+len(row['bits'])))
                maximum+=len(row['bits']);cell['port_directions'][port]='output'
                alias='ng_unused_'+str(len(changes))
                if alias in module['netnames']:raise ValueError('Unused output alias collision')
                module['netnames'][alias]=dict(hide_name=0,bits=cell['connections'][port],attributes={'keep':'1'})
                changes.append(dict(module=name,instance=instance,port=port))
    return changes


def legalize_gw5a_aliases(design):
    """DFFE is a simulation/synthesis alias, not a GW5A physical FF type."""
    changes=[]
    for module,row in design['modules'].items():
        for name,cell in row.get('cells',{}).items():
            if cell['type']!='DFFE':continue
            if set(cell['connections'])!={'D','Q','CLK','CE'}:raise ValueError('Unexpected DFFE alias interface')
            value=cell['parameters'].get('INIT','0')
            # Empty exported INIT denotes the installed model's default 0.
            initial=0 if not value.strip() else int(value,2)
            if initial not in (0,1):raise ValueError('Invalid DFFE initializer')
            cell['type']='DFFRE';cell['connections']['RESET']=['0'];cell['port_directions']['RESET']='input'
            cell['parameters']['INIT']=str(initial)
            changes.append(dict(module=module,instance=name,before='DFFE',after='DFFRE',reset=0,initial=initial))
    return changes


def bind_net_aliases(design,bindings):
    """Keep explicit legacy CST names on the same scalar signal, without edits to CST."""
    import copy
    records=[]
    for legacy,binding in bindings.items():
        if not re.fullmatch(r'[A-Za-z_][\w$]*',legacy):raise ValueError('Unsupported legacy net name')
        module=design['modules'][binding['module']];target=module['netnames'][binding['net']]
        if len(target['bits'])!=1 or not isinstance(target['bits'][0],int):raise ValueError('Legacy alias needs a scalar net')
        bit=target['bits'][0]
        if any(bit in row['bits'] for row in module['ports'].values()):raise ValueError('Do not rename external ports through an internal alias')
        if legacy in module['netnames'] and module['netnames'][legacy]['bits']!=[bit]:raise ValueError('Legacy name collision')
        row=copy.deepcopy(target);row.setdefault('attributes',{})['keep']='1'
        old=[n for n,v in module['netnames'].items() if v['bits']==[bit]]
        for name in old:del module['netnames'][name]
        module['netnames'][legacy]=row
        records.append(dict(legacy=legacy,module=binding['module'],source=binding['net'],
                            bit=bit,replaced_scalar_aliases=old,connections_unchanged=True))
    return records


def name_unaliased_bits(design):
    """Avoid read_json NEW_ID collisions with names retained from an earlier read.

    Only add aliases for integer bits with no existing wire/port name. No pins,
    parameters, attributes of existing objects, or bit identities are changed.
    """
    records=[]
    for mn,m in design['modules'].items():
        names=m.setdefault('netnames',{})
        covered={b for r in list(names.values())+list(m.get('ports',{}).values()) for b in r['bits']}
        bits={b for c in m.get('cells',{}).values() for bs in c['connections'].values() for b in bs if isinstance(b,int)}
        occupied=set(names)|set(m.get('cells',{}))|set(m.get('ports',{}))
        missing=sorted(bits-covered)
        for b in missing:
            base='ng_json_bit_'+str(b);name=base;index=0
            while name in occupied:index+=1;name=base+'_'+str(index)
            names[name]=dict(hide_name=0,bits=[b],attributes={});occupied.add(name)
        if missing:records.append(dict(module=mn,bits_named=len(missing),connections_unchanged=True))
    return records
