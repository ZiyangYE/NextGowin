"""Read primitive port directions from the installed readable simulation source."""
import argparse
import hashlib
import json
from pathlib import Path
import re
from gowin_cones import masked
from split_primitive_models import units


def extract(text):
    result={}
    for name,span in units(text).items():
        body=masked(text[span['start']:span['end']],strings=True)
        header=body[:body.index(';')]
        inner=header[header.index('(')+1:header.rfind(')')]
        directions={}
        if re.search(r'\b(?:input|output|inout)\b',inner):
            current=None
            for part in inner.split(','):
                words=re.findall(r'[A-Za-z_]\w*',re.sub(r'\[[^\]]*\]',' ',part))
                if not words:raise ValueError('Empty ANSI primitive port')
                if words[0] in ('input','output','inout'):current=words[0]
                if current is None:raise ValueError('ANSI port without direction')
                directions[words[-1]]=current
            ports=set(directions)
            body=body[len(header)+1:]
        else:
            ports=set(re.findall(r'[A-Za-z_]\w*',inner))
        body=re.sub(r'\bfunction\b.*?\bendfunction\b|\btask\b.*?\bendtask\b','',body,flags=re.S)
        for kind,names in re.findall(r'^\s*(input|output|inout)\s+([^;]+);',body,re.M):
            names=re.sub(r'\[[^\]]*\]',' ',names)
            for port in re.findall(r'\w+',names):
                if port not in ports:continue
                if port in directions and directions[port]!=kind:raise ValueError('Conflicting primitive port direction')
                directions[port]=kind
        if set(directions)!=ports:raise ValueError('Missing primitive port directions: '+name+' '+repr(ports-set(directions)))
        result[name]=directions
    return result


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--source',type=Path,default=Path('C:/Gowin/Gowin_V1.9.12.04_x64/IDE/simlib/gw5a/prim_sim.v'))
    ap.add_argument('--out',type=Path,required=True);args=ap.parse_args()
    data=args.source.read_bytes();roles=extract(data.decode('utf-8'))
    result=dict(schema='gowin-primitive-port-directions-v1',scope='Port directions only; no timing or functional inference',
        source=str(args.source),source_sha256=hashlib.sha256(data).hexdigest(),ports=roles)
    args.out.parent.mkdir(parents=True,exist_ok=True);args.out.write_text(json.dumps(result,indent=2)+'\n')
    print('Primitive direction tables:',len(roles))


if __name__=='__main__':main()
