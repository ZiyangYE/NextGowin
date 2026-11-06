"""Split installed, readable Gowin simulation sources with exact provenance.

Timing simulation specify blocks describe checks; zero triplets are placeholders
for back annotation, not a device delay library or the C++ STA implementation.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
from gowin_cones import masked


def units(text):
    clean=masked(text,strings=True);items={}
    for match in re.finditer(r'^(module|primitive)\s+(\w+)\b',clean,re.M):
        kind,name=match.groups();end=re.search(r'^end'+kind+r'\b[^\r\n]*',clean[match.end():],re.M)
        if end is None or name in items:raise ValueError('Missing end/duplicate source unit: '+name)
        stop=match.end()+end.end();items[name]=dict(kind=kind,start=match.start(),end=stop)
    return items


def split(directory,out,all_units=False):
    out.mkdir(parents=True,exist_ok=False)
    sources={};indices={};texts={}
    for variant in ['sim','tsim']:
        path=directory/f'prim_{variant}.v';data=path.read_bytes();text=data.decode('utf-8')
        sources[variant]=dict(path=str(path),sha256=hashlib.sha256(data).hexdigest(),bytes=len(data))
        texts[variant]=text;indices[variant]=units(text)
    common=set(indices['sim']) & set(indices['tsim'])
    selected=sorted(set(indices['sim'])|set(indices['tsim'])) if all_units else sorted(n for n in common if re.fullmatch(r'LUT[1-6]|DFF\w*|MUX[248]|ALU|GSR|VCC|GND|mux2',n))
    if not {'LUT4','DFFRE','GSR','mux2'}<=set(selected):raise ValueError('Unexpected simulation library scope')
    result=dict(scope='Verbatim installed primitive simulation source slices. Not C++ STA source; specify delay triplets need back annotation.',
        source=sources,selected_units=selected,units=[],status='running')
    for variant,text in texts.items():
        folder=out/variant;folder.mkdir();index=indices[variant]
        first=min(v['start'] for v in index.values());preamble=text[:first]
        files=[];used_names=set()
        for name in selected:
            if name not in index:continue
            unit=index[name];body=text[unit['start']:unit['end']]
            filename=unit['kind']+'_'+name+'.v'
            if filename.casefold() in used_names:filename=unit['kind']+'_'+name+'_'+hashlib.sha256(name.encode()).hexdigest()[:8]+'.v'
            if filename.casefold() in used_names:raise ValueError('Case-insensitive source filename collision')
            used_names.add(filename.casefold())
            target=folder/filename;target.write_bytes((preamble+body+'\n').encode('utf-8'));files.append(target.name)
            result['units'].append(dict(variant=variant,name=name,kind=unit['kind'],file=str(target.relative_to(out)),
                byte_start=len(text[:unit['start']].encode('utf-8')),byte_end=len(text[:unit['end']].encode('utf-8')),
                line_start=text.count('\n',0,unit['start'])+1,body_sha256=hashlib.sha256(body.encode('utf-8')).hexdigest(),
                output_sha256=hashlib.sha256(target.read_bytes()).hexdigest(),preamble_bytes=len(preamble.encode('utf-8')),
                specify_blocks=len(re.findall(r'\bspecify\b',masked(body))),
                zero_delay_triplets=len(re.findall(r'\b0:0:0\b',masked(body))),
                timing_checks={n:len(re.findall(r'\$'+n+r'\s*\(',masked(body))) for n in ['setuphold','setup','hold','recovery','removal','recrem','width','period']}))
        (folder/'files.f').write_text('\n'.join(files)+'\n')
    result['status']='complete';(out/'manifest.json').write_text(json.dumps(result,indent=2)+'\n')
    return result


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--library',type=Path,default=Path('C:/Gowin/Gowin_V1.9.12.04_x64/IDE/simlib/gw5a'))
    ap.add_argument('--all',action='store_true',dest='all_units',help='Extract all available units, including hard resources')
    ap.add_argument('--out',type=Path,required=True);args=ap.parse_args()
    result=split(args.library,args.out.resolve(),args.all_units)
    print(json.dumps(dict(source=result['source'],selected_unit_count=len(result['selected_units']),files=len(result['units'])),indent=2))


if __name__=='__main__':main()
