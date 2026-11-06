"""Link hash-pinned native IP/GAO macros across the open synthesis boundary."""
import hashlib
import json
from pathlib import Path
import re
from gowin_cones import IDENT,TOKEN,masked
from run_abc_reproduction import ROOT,sha
from verific_bridge import units


def prepare(exported, profile_path, rao):
    profile=json.loads(profile_path.read_text())
    if profile.get('schema')!='native-gao-macro-v1':raise ValueError('Unknown macro binding schema')
    source=ROOT/profile['netlist']
    if sha(source)!=profile['netlist_sha256']:raise ValueError('Native macro netlist changed')
    if rao is None or sha(rao)!=profile['rao_sha256']:raise ValueError('Macro and .rao snapshot disagree')
    for path,digest in profile['generated_inputs'].items():
        if sha(ROOT/path)!=digest:raise ValueError('Generated GAO configuration changed: '+path)
    module=profile['module'];portmap=profile['ports']
    native=source.read_text(encoding='utf-8');index=units(native)
    top_span=index[profile['discard_top']]
    native=native[:top_span[0]]+native[top_span[1]:]
    a,b=units(native)[module];body=native[a:b]
    stop=body.index(';')
    header=body[:stop]
    ports=TOKEN.findall(header[header.index('(')+1:header.rindex(')')])
    ports=[p for p in ports if p!=',']
    if set(ports)!=set(portmap.values()) or len(set(portmap.values()))!=len(portmap):
        raise ValueError('Native macro interface does not match port binding')
    reverse={old:new for new,old in portmap.items()};aliases=[]
    for old,new in reverse.items():
        if old==new:continue
        if re.search(r'\b'+re.escape(new)+r'\b',masked(body)):
            raise ValueError('Native macro port alias collides with internal net: '+new)
        declaration=re.search(r'^(input|output)\s+('+re.escape(old)+r')\s*;',body,re.M)
        if declaration is None:raise ValueError('Only scalar input/output macro renaming supported')
        aliases.append('wire '+old+';\nassign '+(old+' = '+new if declaration[1]=='input' else new+' = '+old)+';')
        body=body[:declaration.start()]+declaration[1]+' '+new+';'+body[declaration.end():]
    stop=body.index(';')
    body=re.sub(r'[A-Za-z_$][\w$]*',lambda m:reverse.get(m[0],m[0]),body[:stop])+body[stop:]
    declarations=list(re.finditer(r'^(?:input|output|inout)\b[^;]*;',body,re.M))
    insertion=declarations[-1].end()
    body=body[:insertion]+'\n'+'\n'.join(aliases)+'\n'+body[insertion:]
    payload=native[:a]+body+native[b:]
    # Replace only this exported macro definition. Its private implementation
    # becomes unreachable; the compiler removes it before resource inference.
    a,b=units(exported)[module];old=exported[a:b];clean=masked(old)
    head=clean[:clean.index(';')]
    declarations=re.findall(r'^\s*(?:input|output|inout)\b[^;]*;',clean,re.M)
    source_ports=TOKEN.findall(head[head.index('(')+1:head.rindex(')')])
    if {p for p in source_ports if p!=','}!=set(portmap):raise ValueError('Exported macro interface changed')
    io=' '.join(profile['external_io_ports'])
    replacement=f'(* blackbox = 1, ng_pad_ports = "{io}" *) '+head+';\n'+'\n'.join(declarations)+'\nendmodule'
    design=exported[:a]+replacement+exported[b:]
    record=dict(profile_sha256=sha(profile_path),native_netlist_sha256=profile['netlist_sha256'],
        macro=module,rao_sha256=profile['rao_sha256'],ports=portmap,
        scope='Native kernel and protected payload retained. Scalar formal aliases added; no native kernel mapping performed by the open mapper.',
        payload_sha256=hashlib.sha256(payload.encode()).hexdigest())
    return design,payload,record
