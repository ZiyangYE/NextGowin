"""Strict LUT-only rewrites of structural Gowin modules, preserving opaque cells.

This is a deliberately bounded mapped-netlist reader, not a Verilog frontend.
Every opaque connection and module port is a cut boundary. Unsupported syntax
is rejected. Original non-LUT statements are retained byte for byte.
"""
from dataclasses import dataclass
import hashlib
import re
from gowin_blif import read_blif


IDENT=r'(?:\\[^\s]+|[a-zA-Z_$][a-zA-Z0-9_$]*)'
TOKEN=re.compile(r'\\[^\s]+|"(?:\\.|[^"\\])*"|\d+\s*\x27[sS]?[bBoOdDhH][0-9a-fA-F_xXzZ?]+|[a-zA-Z_$][a-zA-Z0-9_$]*|\d+|[^\s]')
COMMENT=re.compile(r'"(?:\\.|[^"\\])*"|//[^\r\n]*|/\*.*?\*/',re.S)
LITERAL=re.compile(r"(\d+)\s*'([sS]?)([bBoOdDhH])([0-9a-fA-F_]+)\Z")
GUARDED=re.compile(r'CLK|CLOCK|TCK|RST|RESET|CLEAR|PRESET|GSR',re.I)


def guarded_pin(kind,pin):
    # Verified against the installed GW5A prim_sim/prim_tsim source.
    if (kind,pin) in {('DFFRE','RESET'),('DFFSE','SET')}:return False
    return bool(GUARDED.search(pin))


@dataclass
class Statement:
    start:int
    end:int
    tokens:list
    kind:str
    name:str=''
    pins:dict=None


def masked(text,strings=False):
    return COMMENT.sub(lambda m:m[0] if m[0].startswith('"') and not strings else re.sub(r'[^\r\n]',' ',m[0]),text)


def modules(text):
    clean=masked(text,strings=True);result={}
    for match in re.finditer(r'\bmodule\s+([a-zA-Z_$][\w$]*)\s*\(',clean):
        end=re.search(r'\bendmodule\b',clean[match.end():])
        if end is None:raise ValueError('Missing endmodule')
        if match[1] in result:raise ValueError('Duplicate module definition')
        result[match[1]]=(match.start(),match.end()+end.end())
    return result


def literal(token):
    match=LITERAL.fullmatch(token)
    if not match:raise ValueError('Only fully specified Verilog integer literals supported')
    width=int(match[1]);value=int(match[4].replace('_',''),dict(b=2,o=8,d=10,h=16)[match[3].lower()])
    if width<1 or value.bit_length()>width:raise ValueError('Literal value exceeds width')
    return width,value


def bit(net,index):
    return net+(' ' if net.startswith('\\') else '')+'['+str(index)+']'


def atom(words):
    if len(words)==1:
        if LITERAL.fullmatch(words[0]):
            width,value=literal(words[0])
            if width!=1:raise ValueError('LUT pin requires a scalar')
            return str(value)
        if re.fullmatch(IDENT,words[0]):return words[0]
    if len(words)==4 and words[1]=='[' and words[2].isdigit() and words[3]==']':
        return bit(words[0],words[2])
    raise ValueError('Unsupported scalar connection: '+repr(words))


def connections(words):
    pins={};i=0
    while i<len(words):
        if words[i:i+1]==[',']:i+=1;continue
        if i+3>=len(words) or words[i]!='.' or words[i+2]!='(':
            raise ValueError('Expected named structural cell pins')
        name=words[i+1];i+=3;begin=i;depth=1
        while i<len(words) and depth:
            if words[i]=='(':depth+=1
            elif words[i]==')':depth-=1
            if depth:i+=1
        if depth or name in pins:raise ValueError('Unbalanced/duplicate cell pin')
        pins[name]=words[begin:i];i+=1
    return pins


def parse(text,name,span=None):
    start,end=span if span is not None else modules(text)[name];body=text[start:end];clean=masked(body)
    if '(*' in clean:raise ValueError('Attributes require an explicit preservation policy')
    tokens=list(TOKEN.finditer(clean));statements=[];begin=0
    for i,t in enumerate(tokens):
        if t[0]!=';':continue
        part=tokens[begin:i+1];begin=i+1
        if not part:continue
        words=[p[0] for p in part];kind=words[0]
        statement=Statement(part[0].start(),part[-1].end(),words,kind)
        if kind not in ('module','input','output','inout','wire','defparam','assign'):
            if len(words)<5 or words[2]!='(' or words[-2]!=')':
                raise ValueError('Unsupported structural statement: '+kind)
            statement.name=words[1];statement.pins=connections(words[3:-2])
        statements.append(statement)
    if [t[0] for t in tokens[begin:]]!=['endmodule']:
        raise ValueError('Unexpected module tail')
    header=statements[0].tokens
    if header[:3]!=['module',name,'(']:raise ValueError('Non-ANSI module header required')
    port_names=[x for x in header[3:-2] if x!=',']
    declarations={};packed_order={};ports=set();directions={}
    for s in statements:
        if s.kind not in ('input','output','inout','wire'):continue
        words=s.tokens[1:-1];indices=None;ordered_indices=None
        if words and words[0]=='[':
            if len(words)<5 or not words[1].isdigit() or words[2]!=':' or not words[3].isdigit() or words[4]!=']':
                raise ValueError('Only fixed numeric packed declarations supported')
            a,b=int(words[1]),int(words[3]);indices=list(range(min(a,b),max(a,b)+1))
            ordered_indices=list(range(a,b+(1 if a<=b else -1),1 if a<=b else -1));words=words[5:]
        names=[w for w in words if w!=',']
        if any(not re.fullmatch(IDENT,w) for w in names):raise ValueError('Unsupported net declaration')
        for net in names:
            bits=[net] if indices is None else [bit(net,i) for i in indices]
            if net in declarations and declarations[net]!=bits:raise ValueError('Conflicting net declarations')
            declarations[net]=bits
            ordered=[net] if ordered_indices is None else [bit(net,i) for i in ordered_indices]
            if net in packed_order and packed_order[net]!=ordered:raise ValueError('Conflicting packed net order')
            packed_order[net]=ordered
            if s.kind!='wire':ports.update(bits);directions[net]=s.kind
    if set(port_names)!=set(directions):raise ValueError('Port declarations/header mismatch')
    def touched(words):
        found=set();i=0
        while i<len(words):
            token=words[i]
            if re.fullmatch(IDENT,token):
                if token not in declarations:raise ValueError('Undeclared net '+token)
                if i+1<len(words) and words[i+1]=='[':
                    stop=words.index(']',i+2);sub=words[i+2:stop]
                    if len(sub)==1 and sub[0].isdigit():bits=[bit(token,sub[0])]
                    elif len(sub)==3 and sub[1]==':' and sub[0].isdigit() and sub[2].isdigit():
                        a,b=int(sub[0]),int(sub[2]);bits=[bit(token,n) for n in range(min(a,b),max(a,b)+1)]
                    else:raise ValueError('Unsupported part select')
                    if set(bits)-set(declarations[token]):raise ValueError('Out of range net select')
                    found.update(bits);i=stop+1;continue
                found.update(declarations[token])
            elif token not in ('{','}',',') and not token.isdigit() and not LITERAL.fullmatch(token):
                raise ValueError('Unsupported connection expression '+token)
            i+=1
        return found
    lut=[];opaque=[];initializers={};constants={'0':0,'1':1};boundary=set(ports);guarded=set()
    for port,direction in directions.items():
        if direction!='input' and GUARDED.search(port):guarded.update(declarations[port])
    cell_names=set()
    for s in statements:
        if s.pins is not None:
            if s.name in cell_names:raise ValueError('Duplicate cell name')
            cell_names.add(s.name)
            if re.fullmatch(r'LUT[1-4]',s.kind):
                k=int(s.kind[-1])
                if set(s.pins)!={'F'}|{f'I{i}' for i in range(k)}:raise ValueError('Incomplete LUT ports')
                output=atom(s.pins['F']);inputs=[atom(s.pins[f'I{i}']) for i in range(k)]
                for value in [output]+inputs:
                    if value not in ('0','1') and touched(TOKEN.findall(value))!={value}:
                        raise ValueError('LUT connection must resolve to one declared scalar')
                if output in ('0','1'):raise ValueError('Constant driven by LUT')
                lut.append(dict(statement=s,output=output,inputs=inputs))
            else:
                opaque.append(s)
                for pin,words in s.pins.items():
                    nets=touched(words);boundary.update(nets)
                    if guarded_pin(s.kind,pin):guarded.update(nets)
                if s.kind in ('VCC','GND'):
                    pin='V' if s.kind=='VCC' else 'G'
                    if set(s.pins)!={pin}:raise ValueError('Invalid constant primitive')
                    constants[atom(s.pins[pin])]=int(s.kind=='VCC')
        elif s.kind=='assign':
            words=s.tokens[1:-1]
            if words.count('=')!=1:raise ValueError('Unsupported assign')
            split=words.index('=');boundary.update(touched(words[:split]));boundary.update(touched(words[split+1:]))
        elif s.kind=='defparam':
            words=s.tokens
            if len(words)!=7 or words[2]!='.' or words[4]!='=':
                raise ValueError('Unsupported defparam syntax')
            initializers.setdefault(words[1],[]).append(s)
    lut_outputs={n['output'] for n in lut}
    if len(lut_outputs)!=len(lut) or lut_outputs & constants.keys():raise ValueError('Multiple LUT/constant drivers')
    for net,direction in directions.items():
        if direction=='input' and lut_outputs & set(declarations[net]):raise ValueError('LUT drives module input')
    by_output={n['output']:n for n in lut};protected=set();stack=list(guarded)
    while stack:
        signal=stack.pop()
        if signal in by_output and signal not in protected:
            protected.add(signal);stack.extend(by_output[signal]['inputs'])
    removed=[];selected=[]
    for node in lut:
        s=node['statement'];params=initializers.get(s.name,[])
        if len(params)!=1 or params[0].tokens[3]!='INIT':raise ValueError('LUT must have exactly one INIT defparam')
        width,truth=literal(params[0].tokens[5])
        if width!=(1<<len(node['inputs'])):raise ValueError('INIT width/LUT arity mismatch')
        node['truth']=truth
        if node['output'] in protected:
            opaque.append(s);boundary.update(node['inputs']);boundary.add(node['output'])
        else:
            selected.append(node);removed.extend([s,params[0]])
    lut=selected;lut_outputs={n['output'] for n in lut}
    outputs=sorted(lut_outputs & boundary)
    inputs=sorted({p for n in lut for p in n['inputs']}-lut_outputs-constants.keys())
    names={p:f'pi{i}' for i,p in enumerate(inputs)}
    names.update({p:f'po{i}' for i,p in enumerate(outputs)})
    for n in lut:
        names.setdefault(n['output'],'ln'+str(len(names)))
    used_constants={p for n in lut for p in n['inputs'] if p in constants}
    for p in sorted(used_constants):names[p]='cn'+str(len(names))
    lines=['.model cone','.inputs '+' '.join(names[p] for p in inputs),'.outputs '+' '.join(names[p] for p in outputs)]
    for p in sorted(used_constants):
        lines.append('.names '+names[p])
        if constants[p]:lines.append('1')
    for n in lut:
        k=len(n['inputs'])
        if n['truth'] in (0,(1<<(1<<k))-1):
            lines.append('.names '+names[n['output']])
            if n['truth']:lines.append('1')
            continue
        lines.append('.names '+' '.join([names[p] for p in n['inputs']]+[names[n['output']]]))
        for value in range(1<<k):
            if (n['truth']>>value)&1:lines.append(''.join(str((value>>i)&1) for i in range(k))+' 1')
    return dict(name=name,start=start,end=end,body=body,statements=statements,luts=lut,opaque=opaque,
        removed=removed,inputs=inputs,outputs=outputs,names=names,cell_names=cell_names,constants=constants,
        protected_luts=len(protected),
        declarations=declarations,packed_order=packed_order,directions=directions,port_names=port_names,
        blif='\n'.join(lines+['.end'])+'\n')


def supports(network):
    values={p:{p} for p in network['inputs']};nodes={n['output']:n for n in network['nodes']}
    for name in network['topological']:
        node=nodes[name];k=len(node['inputs']);truth=node['truth']
        active=[p for i,p in enumerate(node['inputs']) if any(((truth>>x)&1)!=((truth>>(x^(1<<i)))&1) for x in range(1<<k))]
        values[name]=set().union(*(values[p] for p in active))
    return {p:values[p] for p in network['outputs']}


def replace(text,parsed,mapped_path,reference_path):
    mapped=read_blif(mapped_path);reference=read_blif(reference_path)
    if mapped['inputs']!=reference['inputs'] or mapped['outputs']!=reference['outputs']:
        raise ValueError('Mapper changed the ordered cut interface')
    before,after=supports(reference),supports(mapped)
    if any(after[p]-before[p] for p in after):raise ValueError('Replacement adds boundary dependency')
    reserved=TOKEN.findall(masked(parsed['body']));prefix='nextgowin_'
    while any(w.lstrip('\\').startswith(prefix) for w in reserved):prefix='_'+prefix
    original_by_name={parsed['names'][p]:p for p in parsed['inputs']+parsed['outputs']}
    signals=dict(original_by_name)
    for n in mapped['nodes']:signals.setdefault(n['output'],prefix+'n'+str(len(signals)))
    def wire(net):return net+' ' if net.startswith('\\') else net
    internal=[v for k,v in signals.items() if k not in original_by_name]
    lines=['\n// NextGowin: CEC-verified LUT replacement; other cell statements retained.']
    if internal:lines.append('wire '+','.join(internal)+';')
    for i,node in enumerate(mapped['nodes']):
        k=len(node['inputs']);name=prefix+'lut'+str(i);out=wire(signals[node['output']])
        if k==0:
            lines.append(f'{"VCC" if node["truth"] else "GND"} {name}(.{"V" if node["truth"] else "G"}({out}));')
        else:
            pins=','.join(f'.I{j}({wire(signals[p])})' for j,p in enumerate(node['inputs']))
            lines.extend([f'LUT{k} {name}({pins},.F({out}));',f"defparam {name}.INIT={1<<k}'h{node['truth']:0{max(1,(1<<k)//4)}X};"])
    body=parsed['body']
    for s in sorted(parsed['removed'],key=lambda s:s.start,reverse=True):body=body[:s.start]+body[s.end:]
    end=masked(body).rfind('endmodule')
    body=body[:end]+'\n'.join(lines)+'\n'+body[end:]
    candidate=text[:parsed['start']]+body+text[parsed['end']:]
    check=parse(candidate,parsed['name'])
    # The transform copies every original opaque cell statement verbatim.
    retained=[check['body'][s.start:s.end] for s in check['opaque']]
    for s in parsed['opaque']:
        if parsed['body'][s.start:s.end] not in retained:raise ValueError('Opaque cell changed')
    if candidate[:parsed['start']]!=text[:parsed['start']] or candidate[check['end']:]!=text[parsed['end']:]:
        raise ValueError('Another module changed')
    return candidate,check,dict(opaque_cells=len(parsed['opaque']),opaque_statements_sha256=hashlib.sha256(
        ''.join(parsed['body'][s.start:s.end] for s in parsed['opaque']).encode()).hexdigest(),
        original_luts=len(parsed['luts']),replacement_luts=len(check['luts']),new_boundary_dependencies=False)


def readback(parsed,reference):
    """Re-extract actual emitted INITs while keeping unused original cut PIs."""
    if set(parsed['inputs'])-set(reference['inputs']):raise ValueError('Readback gained cut inputs')
    if set(reference['outputs'])-set(parsed['outputs'])-parsed['constants'].keys():
        raise ValueError('Readback lost a cut output driver')
    rename={}
    for net,name in parsed['names'].items():
        rename[name]=reference['names'].get(net,'rb'+str(len(rename)))
    lines=['.model readback','.inputs '+' '.join(reference['names'][p] for p in reference['inputs']),
           '.outputs '+' '.join(reference['names'][p] for p in reference['outputs'])]
    driven=set()
    for line in parsed['blif'].splitlines():
        if line.startswith('.names '):
            words=[rename[x] for x in line.split()[1:]];driven.add(words[-1])
            lines.append('.names '+' '.join(words))
        elif line and not line.startswith('.'):lines.append(line)
    for net in set(reference['outputs'])-set(parsed['outputs']):
        if reference['names'][net] in driven:continue
        lines.append('.names '+reference['names'][net])
        if parsed['constants'][net]:lines.append('1')
    return '\n'.join(lines+['.end'])+'\n'
