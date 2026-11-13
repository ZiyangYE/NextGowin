"""Elaborate a mapped structural hierarchy without changing its leaf graph.

No RTL inference or Boolean optimization. Wire aliases and unsigned packed
ports are resolved bit by bit; unknown literals, inouts and width mismatches
are rejected. A reverse read checks the emitted leaf graph and root interface.
"""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import re
from gowin_cones import parse,modules,bit,LITERAL,literal
from run_abc_reproduction import sha


class Union:
    def __init__(self):self.parent={};self.rank={}
    def add(self,x):
        if x not in self.parent:self.parent[x]=x;self.rank[x]=0
        return x
    def find(self,x):
        self.add(x);root=x
        while self.parent[root]!=root:root=self.parent[root]
        while self.parent[x]!=x:
            parent=self.parent[x];self.parent[x]=root;x=parent
        return root
    def join(self,a,b):
        a,b=self.find(a),self.find(b)
        if a==b:return
        if self.rank[a]<self.rank[b]:a,b=b,a
        self.parent[b]=a
        if self.rank[a]==self.rank[b]:self.rank[a]+=1


def expression(words,parsed):
    """Return packed bits in expression order, most significant first."""
    pos=0
    def value():
        nonlocal pos
        if pos>=len(words):raise ValueError('Incomplete packed expression')
        token=words[pos];pos+=1
        if token=='{':
            if pos+1<len(words) and words[pos].isdigit() and words[pos+1]=='{':
                count=int(words[pos]);pos+=1;inside=value()
                if not 1<=count<=65536 or pos>=len(words) or words[pos]!='}':raise ValueError('Invalid replication')
                pos+=1;return inside*count
            bits=[]
            while True:
                bits.extend(value())
                if pos>=len(words):raise ValueError('Missing concatenation close')
                delimiter=words[pos];pos+=1
                if delimiter=='}':return bits
                if delimiter!=',':raise ValueError('Invalid concatenation delimiter')
        if LITERAL.fullmatch(token):
            width,number=literal(token)
            return [str((number>>i)&1) for i in reversed(range(width))]
        if token.isdigit():raise ValueError('Unsized literal in connection needs explicit sizing')
        if token not in parsed['packed_order']:raise ValueError('Undeclared packed expression '+token)
        if pos<len(words) and words[pos]=='[':
            pos+=1;begin=pos
            while pos<len(words) and words[pos]!=']':pos+=1
            if pos==len(words):raise ValueError('Missing packed select close')
            sub=words[begin:pos];pos+=1
            if len(sub)==1 and sub[0].isdigit():selected=[bit(token,int(sub[0]))]
            elif len(sub)==3 and sub[1]==':' and sub[0].isdigit() and sub[2].isdigit():
                a,b=int(sub[0]),int(sub[2]);step=1 if a<=b else -1;selected=[bit(token,i) for i in range(a,b+step,step)]
            else:raise ValueError('Unsupported packed select')
            if set(selected)-set(parsed['declarations'][token]):raise ValueError('Out of range packed select')
            return selected
        return list(parsed['packed_order'][token])
    if not words:return []
    result=value()
    if pos!=len(words):raise ValueError('Unexpected packed expression tail')
    return result


def parameters(parsed):
    result={}
    for s in parsed['statements']:
        if s.kind!='defparam':continue
        _,cell,_,name,_,value,_=s.tokens
        if name in result.setdefault(cell,{}):raise ValueError('Duplicate structural parameter')
        result[cell][name]=value
    return result


class Elaborator:
    def __init__(self,text):
        self.text=text;self.index=modules(text);self.cache={};self.uf=Union();self.leaves=[];self.instances=[]
    def module(self,name):
        if name not in self.cache:self.cache[name]=parse(self.text,name,self.index[name])
        return self.cache[name]
    def key(self,path,signal):
        key=('literal',signal) if signal in ('0','1') else ('wire',path,signal)
        return self.uf.add(key)
    def walk(self,name,path=(),ancestors=()):
        if name in ancestors:raise ValueError('Recursive structural module')
        parsed=self.module(name)
        if 'inout' in parsed['directions'].values():raise ValueError('Inout hierarchy cannot be flattened by the wire-alias proof')
        for signals in parsed['packed_order'].values():
            for signal in signals:self.key(path,signal)
        local_params=parameters(parsed);self.instances.append(dict(path=list(path),module=name))
        for statement in parsed['statements']:
            if statement.kind=='assign':
                words=statement.tokens[1:-1];split=words.index('=')
                left,right=expression(words[:split],parsed),expression(words[split+1:],parsed)
                if len(left)!=len(right):raise ValueError('Assign width mismatch')
                for a,b in zip(left,right):self.uf.join(self.key(path,a),self.key(path,b))
            elif statement.pins is not None:
                pins={pin:expression(words,parsed) for pin,words in statement.pins.items()}
                if statement.kind in self.index:
                    if local_params.get(statement.name):raise ValueError('Mapped child parameter overrides require explicit elaboration')
                    child=self.module(statement.kind);child_path=path+(statement.name,)
                    if set(pins)-set(child['directions']):raise ValueError('Unknown child port')
                    for port,direction in child['directions'].items():
                        actual=pins.get(port,[]);formal=child['packed_order'][port]
                        if not actual:
                            if direction=='input':raise ValueError('Unconnected child input requires a value model')
                            continue
                        if len(actual)!=len(formal):
                            raise ValueError(f'Child width mismatch: {name}/{statement.name}/{port}: {len(actual)} vs {len(formal)}')
                        for a,b in zip(actual,formal):self.uf.join(self.key(path,a),self.key(child_path,b))
                    self.walk(statement.kind,child_path,ancestors+(name,))
                else:
                    self.leaves.append(dict(path=path+(statement.name,),kind=statement.kind,
                        pins={pin:[self.key(path,signal) for signal in signals] for pin,signals in pins.items()},
                        parameters=local_params.get(statement.name,{})))


def validate_drivers(elaborator,root,roles):
    """Wire-alias elaboration requires single-driver, non-tristate logic."""
    uf=elaborator.uf;drivers={};uses=set();parsed=elaborator.module(root)
    def drive(key,origin,value=None):drivers.setdefault(uf.find(key),[]).append((origin,value))
    for port,direction in parsed['directions'].items():
        for signal in parsed['packed_order'][port]:
            key=elaborator.key((),signal)
            if direction=='input':drive(key,('input',signal))
            else:uses.add(uf.find(key))
    for key in list(uf.parent):
        if key[0]=='literal':drive(key,('literal',key[1]),int(key[1]))
    for index,leaf in enumerate(elaborator.leaves):
        if leaf['kind'] not in roles:raise ValueError('Missing primitive directions: '+leaf['kind'])
        for pin,signals in leaf['pins'].items():
            direction=roles[leaf['kind']].get(pin)
            if direction not in ('input','output'):raise ValueError('Unknown/inout leaf pin: '+leaf['kind']+'/'+pin)
            for bit_index,key in enumerate(signals):
                if direction=='output':
                    constant=int(leaf['kind']=='VCC') if leaf['kind'] in ('VCC','GND') else None
                    drive(key,('leaf',index,pin,bit_index),constant)
                else:uses.add(uf.find(key))
    for signals in drivers.values():
        values=[value for _,value in signals]
        if len(signals)>1 and (None in values or len(set(values))>1):
            raise ValueError('Multiple independent drivers in flattened wire class: '+repr(signals[:3]))
    undriven=uses-drivers.keys()
    if undriven:raise ValueError('Consumed undriven wire classes: '+repr(list(undriven)[:3]))
    return dict(single_driver_wire_classes=True,consumed_undriven_classes=0,primitive_pin_directions_known=True)


def emitted_graph(text,root):
    parsed=parse(text,root);uf=Union()
    def key(signal):return uf.add(signal)
    for nets in parsed['packed_order'].values():
        for net in nets:key(net)
    for s in parsed['statements']:
        if s.kind=='assign':
            words=s.tokens[1:-1];split=words.index('=')
            a,b=expression(words[:split],parsed),expression(words[split+1:],parsed)
            if len(a)!=len(b):raise ValueError('Readback assign width mismatch')
            for left,right in zip(a,b):uf.join(key(left),key(right))
    params=parameters(parsed);cells={}
    for s in parsed['statements']:
        if s.pins is not None:
            cells[s.name]=dict(kind=s.kind,pins={pin:[uf.find(key(n)) for n in expression(words,parsed)] for pin,words in s.pins.items()},parameters=params.get(s.name,{}))
    return parsed,uf,cells


def flatten(text,root,top,port_roles=None):
    elaborator=Elaborator(text);original=elaborator.module(root);elaborator.walk(root)
    if port_roles is None:
        path=Path(__file__).resolve().parents[1]/'results/timing/primitive_ports.json'
        port_roles=json.loads(path.read_text())['ports']
    driver_checks=validate_drivers(elaborator,root,port_roles)
    driver_checks['primitive_directions_sha256']=hashlib.sha256(json.dumps(port_roles,sort_keys=True).encode()).hexdigest()
    uf=elaborator.uf;groups={}
    for signal in list(uf.parent):groups.setdefault(uf.find(signal),[]).append(signal)
    public={bit_name:direction for port,direction in original['directions'].items() for bit_name in original['packed_order'][port]}
    prefix='ngflat_'
    while any(name.lstrip('\\').startswith(prefix) for name in original['port_names']):prefix='_'+prefix
    labels={};aliases=[];constant_classes={}
    for representative,signals in groups.items():
        ports=[key[2] for key in signals if key[0]=='wire' and key[1]==() and key[2] in public]
        ports.sort(key=lambda signal:(public[signal]!='input',signal))
        if sum(public[p]=='input' for p in ports)>1:raise ValueError('Aliased independent root inputs')
        values={key[1] for key in signals if key[0]=='literal'}
        if len(values)>1:raise ValueError('Contradictory constant aliases')
        label=ports[0] if ports else prefix+'n'+str(len(labels));labels[representative]=label
        for port in ports[1:]:aliases.append((port,label))
        if values:constant_classes[representative]=int(next(iter(values)))
    def signal(key):return labels[uf.find(key)]
    def wire(name):return name+' ' if name.startswith('\\') else name
    def vector(keys):
        parts=[wire(signal(k)) for k in keys]
        return parts[0] if len(parts)==1 else '{'+','.join(parts)+'}' if parts else ''
    lines=[original['body'][s.start:s.end] for s in original['statements'] if s.kind in ('module','input','output')]
    private=[label for label in labels.values() if label not in public]
    for start in range(0,len(private),128):lines.append('wire '+','.join(private[start:start+128])+';')
    expected={};mapping=[];known_constants=dict(constant_classes)
    for number,leaf in enumerate(elaborator.leaves):
        name=prefix+'c'+str(number)
        lines.append(leaf['kind']+' '+name+'('+','.join('.'+pin+'('+vector(keys)+')' for pin,keys in leaf['pins'].items())+');')
        for parameter,value in leaf['parameters'].items():lines.append(f'defparam {name}.{parameter}={value};')
        expected[name]=dict(kind=leaf['kind'],pins={pin:[signal(k) for k in keys] for pin,keys in leaf['pins'].items()},parameters=leaf['parameters'])
        mapping.append(dict(name=name,original_path=list(leaf['path']),kind=leaf['kind']))
        if leaf['kind'] in ('GND','VCC'):
            pin='G' if leaf['kind']=='GND' else 'V';keys=leaf['pins'][pin]
            if len(keys)!=1:raise ValueError('Invalid constant leaf output')
            representative=uf.find(keys[0]);value=int(leaf['kind']=='VCC')
            if representative in known_constants and known_constants[representative]!=value:raise ValueError('Contradictory leaf constant')
            known_constants[representative]=value
            constant_classes.pop(representative,None)
    for representative,value in constant_classes.items():
        name=prefix+'literal'+str(value);suffix=0
        while name in expected:suffix+=1;name=prefix+'literal'+str(value)+'_'+str(suffix)
        kind,pin=('VCC','V') if value else ('GND','G');label=labels[representative]
        lines.append(f'{kind} {name}(.{pin}({wire(label)}));')
        expected[name]=dict(kind=kind,pins={pin:[label]},parameters={})
    for port,label in aliases:lines.append(f'assign {wire(port)}={wire(label)};')
    lines.append('endmodule');body='\n'.join(lines)
    candidate=text[:original['start']]+body+text[original['end']:]
    # Prune definitions that became unreachable; keep the original top unambiguous.
    after=modules(candidate);reachable=set();stack=[top]
    while stack:
        name=stack.pop()
        if name in reachable:continue
        reachable.add(name)
        parsed=parse(candidate,name,after[name])
        stack.extend(s.kind for s in parsed['statements'] if s.pins is not None and s.kind in after)
    if root not in reachable:raise ValueError('Flattened core is not reachable from the selected top')
    pruned=set(after)-reachable
    for name in sorted(pruned,key=lambda name:after[name][0],reverse=True):
        start,end=after[name];candidate=candidate[:start]+candidate[end:]
    emitted,reverse,cells=emitted_graph(candidate,root)
    if set(cells)!=set(expected):raise ValueError('Flattening changed the leaf instance set')
    for name,leaf in expected.items():
        canonical=dict(leaf,pins={pin:[reverse.find(n) for n in nets] for pin,nets in leaf['pins'].items()})
        if cells[name]!=canonical:raise ValueError('Leaf graph readback mismatch: '+name)
    for representative,signals in groups.items():
        ports=[key[2] for key in signals if key[0]=='wire' and key[1]==() and key[2] in public]
        for port in ports:
            if reverse.find(port)!=reverse.find(labels[representative]):raise ValueError('Root output alias changed')
    # Outside the replaced core, every reachable module body must be unchanged.
    final_index=modules(candidate)
    for name in reachable-{root}:
        a,b=elaborator.index[name];c,d=final_index[name]
        if text[a:b]!=candidate[c:d]:raise ValueError('Module outside core changed')
    metadata=dict(root=root,top=top,original_module_count=len(elaborator.index),remaining_module_count=len(final_index),
        pruned_modules=sorted(pruned),flattened_instance_count=len(elaborator.instances),original_leaf_count=len(elaborator.leaves),
        emitted_leaf_count=len(expected),leaf_types=dict(Counter(leaf['kind'] for leaf in expected.values())),
        preserved_leaf_parameters_and_bit_connections=True,root_port_aliases_preserved=True,
        other_reachable_module_bodies_byte_equal=True,
        driver_checks=driver_checks,
        scope='Structural leaf-graph identity after unsigned exact-width port/assign elaboration; no Boolean optimization or physical-timing claim')
    return candidate,metadata,mapping


def main():
    ap=argparse.ArgumentParser();ap.add_argument('input',type=Path);ap.add_argument('--root',required=True)
    ap.add_argument('--top',required=True);ap.add_argument('--out',type=Path,required=True);args=ap.parse_args()
    out=args.out.resolve();out.mkdir(parents=True,exist_ok=False)
    source=args.input.read_bytes().decode('utf-8');candidate,result,mapping=flatten(source,args.root,args.top)
    target=out/'flattened.vg';target.write_bytes(candidate.encode('utf-8'))
    result.update(status='complete',input_sha256=sha(args.input),output_sha256=sha(target),source_sha256=sha(Path(__file__)))
    (out/'leaf_mapping.json').write_text(json.dumps(mapping,indent=2)+'\n')
    result['leaf_mapping_sha256']=sha(out/'leaf_mapping.json')
    (out/'manifest.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps({k:v for k,v in result.items() if k!='pruned_modules'},indent=2))


if __name__=='__main__':main()
