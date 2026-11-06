"""Recover checked 1-D memory arrays from Verific read/write helper modules."""
from collections import defaultdict
import math
import re
from gowin_cones import IDENT, TOKEN, connections, masked

HELPER = re.compile(r'(ReadPort|ClockedWritePort)_(\d+)_(\d+)_(\d+)_(\d+)_(\d+)_(\d+)\Z')


def memory_attributes(declaration):
    """Retain explicit source memory intent exported in Verific attributes."""
    attributes={}
    styles=re.findall(r'\bram_style\s*=\s*"([^"]+)"',declaration)
    if len(set(styles))>1:raise ValueError('Conflicting memory styles')
    if styles:
        if styles[0] not in ('block','distributed','logic','auto','registers'):raise ValueError('Unsupported memory style: '+styles[0])
        attributes['ram_style']='logic' if styles[0]=='registers' else styles[0]
    if re.search(r'\bsyn_ramstyle\s*=\s*"registers"',declaration):
        if attributes.get('ram_style','logic') not in ('logic','auto'):raise ValueError('Conflicting register/block memory intent')
        attributes['ram_style']='logic'
    checks=re.findall(r'\bno_rw_check\s*=\s*([^,\s*]+)',declaration)
    if checks:
        if len(set(checks))!=1 or checks[0] not in ('0','1'):raise ValueError('Unsupported memory collision-check attribute')
        attributes['no_rw_check']=int(checks[0])
    return attributes


def restore(text, unit_index):
    index = unit_index(text)
    helpers = {}
    for name, span in index.items():
        match = HELPER.fullmatch(name)
        if not match:
            continue
        kind, aw, width, left, right, high, low = match.groups()
        aw,width,left,right,high,low = map(int,(aw,width,left,right,high,low))
        body = masked(text[slice(*span)])
        if high < low or low != 0:
            raise ValueError('Unsupported memory packed orientation')
        word = high-low+1
        prefix = 'write' if kind=='ClockedWritePort' else 'read'
        address = prefix+'_address'
        partial = width != word
        colbits = math.ceil(math.log2(word)) if partial else 0
        if partial and width != 1:
            raise ValueError('Unsupported partial memory transfer width')
        row = f'{address}[{aw-1}:{colbits}]'
        row_offset = f'-({row}-({right}))' if left <= right else f'({row}-({right}))'
        expected = row_offset+f'*{word}'
        if partial:
            expected += f'+({address}[{colbits-1}:0]-({low}))*1'
        actual = re.search(r'Ram\[(.*?)\+:\s*'+str(width)+r'\]', body, re.S)
        if actual is None or re.sub(r'\s+','',actual[1]) != expected:
            raise ValueError('Unknown memory helper offset semantics: '+name)
        if len(re.findall(r'\balways\b',body)) != 1:
            raise ValueError('Unexpected memory helper processes')
        helpers[name] = dict(kind=kind,aw=aw,width=width,left=left,right=right,word=word,
                             colbits=colbits,partial=partial)
    changes = []
    records = []
    call_pattern = re.compile(r'^[ \t]*((?:ReadPort|ClockedWritePort)_\w+)\s+('+IDENT+r')\s*\((.*?)\)\s*(?:/\*.*?\*/\s*)?;', re.M|re.S)
    for name, (a,b) in index.items():
        if name in helpers:
            changes.append((a,b,''))
            continue
        body = text[a:b]
        matches = list(call_pattern.finditer(body))
        if not matches:
            continue
        groups = defaultdict(list)
        for match in matches:
            if match[1] not in helpers:
                raise ValueError('Unknown memory helper name: '+match[1])
            pins = connections(TOKEN.findall(match[3]))
            ram = [p for p in pins['Ram'] if p not in ('{','}')]
            if len(ram)!=1 or not re.fullmatch(IDENT,ram[0]):
                raise ValueError('Memory helper needs a named full vector')
            groups[ram[0]].append((match,pins,helpers[match[1]]))
        edits = []
        for group_index,(ram,calls) in enumerate(groups.items()):
            spec = calls[0][2]
            if any(any(s[k]!=spec[k] for k in ('left','right','word')) for _,_,s in calls):
                raise ValueError('Read/write memory geometry disagrees')
            array = 'ng_memory_'+str(group_index)
            if re.search(r'\b'+array+r'\b',body):
                raise ValueError('Memory name collision')
            depth = abs(spec['right']-spec['left'])+1
            declaration = re.search(r'^[ \t]*(?:wire|reg)\s*\[\s*'+str(depth*spec['word']-1)+r'\s*:\s*0\s*\]\s*'+re.escape(ram)+r'(?=\s|/|;)\s*[^;]*;',body,re.M)
            if not declaration:
                raise ValueError('Memory vector declaration not recognized: '+ram)
            # Require all uses to be the declaration and verified helper ports.
            remaining = body
            for match,_,_ in reversed(calls):
                remaining = remaining[:match.start()]+remaining[match.end():]
            remaining = remaining.replace(declaration[0],'',1)
            if re.search(r'(?<!\S)'+re.escape(ram)+r'(?=\s|[;,)\]])',masked(remaining)):
                raise ValueError('Memory vector has additional visible uses: '+ram)
            attributes=memory_attributes(declaration[0])
            style=('(* '+', '.join(k+' = '+('"'+v+'"' if isinstance(v,str) else str(v)) for k,v in attributes.items())+' *) ') if attributes else ''
            replacement = style+f"reg [{spec['word']-1}:0] {array} [{spec['left']}:{spec['right']}];"
            initial = re.search(r'/\* Init Val:\s*([01xXzZ]+)\s*\*/', declaration[0])
            if initial:
                value=initial[1]
                if len(value)!=depth*spec['word']:
                    raise ValueError('Memory initializer width mismatch')
                rows=range(spec['left'],spec['right']+(1 if spec['left']<=spec['right'] else -1),1 if spec['left']<=spec['right'] else -1)
                replacement+='\ninitial begin\n'+'\n'.join(f"{array}[{row}] = {spec['word']}'b{value[i*spec['word']:(i+1)*spec['word']]};" for i,row in enumerate(rows))+'\nend'
            edits.append((declaration.start(),declaration.end(),replacement))
            for port_index,(match,pins,spec) in enumerate(calls):
                address=array+'_addr_'+str(port_index)
                prefix='write' if spec['kind']=='ClockedWritePort' else 'read'
                expr=lambda p:' '.join(pins[p])
                replacement=f"wire [{spec['aw']-1}:0] {address} = {expr(prefix+'_address')};\n"
                row=f"{address}[{spec['aw']-1}:{spec['colbits']}]"
                target=f'{array}[{row}]'
                if spec['partial']:
                    # The target project's bit-write ports use a constant lane.
                    # Reject a variable lane: out-of-range bit indices in the
                    # linear export could otherwise alias an adjacent row.
                    tokens=pins['write_address']
                    suffix=re.search(r"(\d+)'[bB]([01_]+)\s*\}\s*$",expr('write_address'))
                    if not suffix or int(suffix[1])<spec['colbits'] or int(suffix[2].replace('_',''),2)>=spec['word']:
                        raise ValueError('Partial memory writes require a checked constant column')
                    target+=f"[{address}[{spec['colbits']-1}:0]]"
                if prefix=='write':
                    replacement+=f"always @(posedge {expr('clk')}) if ({expr('write_enable')}) {target} <= {expr('write_data')};"
                else:
                    temp=array+'_read_'+str(port_index)
                    replacement+=f"reg [{spec['width']-1}:0] {temp};\nalways @* if ({expr('read_enable')}) {temp} = {target};\nassign {expr('read_data')} = {temp};"
                edits.append((match.start(),match.end(),replacement))
            records.append(dict(module=name,net=ram,depth=depth,width=spec['word'],ports=len(calls),initialized=bool(initial),restored_attributes=attributes))
        for start,end,replacement in sorted(edits,reverse=True):
            body=body[:start]+replacement+body[end:]
        changes.append((a,b,body))
    chunks=[]
    cursor=0
    for a,b,replacement in sorted(changes):
        chunks.extend([text[cursor:a],replacement]);cursor=b
    chunks.append(text[cursor:])
    return ''.join(chunks),records
