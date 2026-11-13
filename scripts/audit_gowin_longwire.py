"""Read-only PE evidence for long-wire assignment and routing, not recovered C++.

All addresses are RVAs. Raw rel32 candidates are accepted only when the
containing PE unwind range disassembles to a call at that exact boundary.
"""
import argparse
from bisect import bisect_right
from collections import defaultdict
import hashlib
import json
from pathlib import Path
import re
import struct
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'binary_audit/python_libs'))
import capstone
import pefile


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--dll',type=Path,default=Path('C:/Gowin/Gowin_V1.9.12.04_x64/IDE/bin/gowin.dll'))
    ap.add_argument('--out',type=Path,required=True)
    ap.add_argument('--rva',type=lambda s:int(s,0),action='append',default=[],help='Additional audited function start RVA')
    ap.add_argument('--string-pattern',default=r'long.?wire|long.?line|\blw\b|\blw_|\bLW[A-Z_]*\b|BUFS|bufs|longwire',help='Diagnostic ASCII regex')
    args=ap.parse_args()
    data=args.dll.read_bytes()
    pe=pefile.PE(str(args.dll),max_symbol_exports=100000)
    exports={e.name.decode(errors='replace'):e.address for e in pe.DIRECTORY_ENTRY_EXPORT.symbols if e.name}
    if len(exports)!=pe.DIRECTORY_ENTRY_EXPORT.struct.NumberOfNames:
        raise ValueError('Incomplete export inventory')
    names=defaultdict(list)
    for name,rva in exports.items():names[rva].append(name)
    ranges=sorted((r.struct.BeginAddress,r.struct.EndAddress) for r in pe.DIRECTORY_ENTRY_EXCEPTION)
    begins=[a for a,b in ranges]
    def extent(rva):
        k=bisect_right(begins,rva)-1
        return ranges[k] if k>=0 and ranges[k][0]<=rva<ranges[k][1] else None
    md=capstone.Cs(capstone.CS_ARCH_X86,capstone.CS_MODE_64)
    decoded={}
    def instructions(rva):
        span=extent(rva)
        if span is None:return []
        a,b=span
        if a not in decoded:decoded[a]=list(md.disasm(pe.get_data(a,b-a),a))
        return decoded[a]
    strings=[]
    for m in re.finditer(rb'[ -~]{5,}',data):
        value=m.group().decode()
        if len(value)<=1400 and not value.startswith('?') and re.search(args.string_pattern,value,re.I):
            strings.append(dict(text=value,rva=pe.get_rva_from_offset(m.start())))
    selected={n:r for n,r in exports.items() if re.search(r'Longwire|LongWire|LwUsed|Lw.*5AT138|[Ll]w[Rr]oute|[Ll]w[Nn]et|[Ll]w[Rr]emain|[Ll]w[Cc]andidate|[Ll]ong[Ww]ires|[Ll]w[Aa]ssign',n)}
    string_by_rva={r['rva']:r for r in strings}
    xrefs=[]
    # RIP-relative LEA is the usual MSVC string-address load.
    for section in pe.sections:
        if not section.Characteristics&0x20000000:continue
        raw=section.get_data();base=section.VirtualAddress
        for m in re.finditer(rb'[\x48-\x4f]\x8d[\x05\x0d\x15\x1d\x25\x2d\x35\x3d]',raw):
            pos=m.start()
            if pos+7>len(raw):continue
            target=base+pos+7+struct.unpack_from('<i',raw,pos+3)[0]
            if target not in string_by_rva:continue
            at=base+pos
            ins=next((i for i in instructions(at) if i.address==at and i.size==7 and i.mnemonic=='lea'),None)
            if ins is None:continue
            span=extent(at)
            xrefs.append(dict(at=at,target=target,text=string_by_rva[target]['text'],function=span[0],symbols=names.get(span[0],[])))
    targets=set(selected.values())|{r['function'] for r in xrefs}|set(args.rva)
    calls=[]
    for section in pe.sections:
        if not section.Characteristics&0x20000000:continue
        raw=section.get_data();base=section.VirtualAddress
        for m in re.finditer(b'\xe8',raw):
            pos=m.start()
            if pos+5>len(raw):continue
            target=base+pos+5+struct.unpack_from('<i',raw,pos+1)[0]
            if target not in targets:continue
            at=base+pos
            ins=next((i for i in instructions(at) if i.address==at and i.size==5 and i.mnemonic=='call'),None)
            if ins is None:continue
            span=extent(at)
            calls.append(dict(at=at,target=target,callee=names.get(target,[]),function=span[0],symbols=names.get(span[0],[])))
    args.out.mkdir(parents=True,exist_ok=False)
    evidence=[]
    dump_targets=targets|{r['function'] for r in calls}
    for rva in sorted(dump_targets):
        span=extent(rva)
        a,b=span if span else (rva,rva+32)
        # Explicit named functions can have multiple unwind fragments, and
        # leaf accessors have none. Bound these by the next exported entry.
        bound='PE unwind range'
        if rva in names:
            later=min((r for r in names if r>rva),default=rva+32)
            if later-rva<=0x20000:
                a,b=rva,later;bound='Next exported entry; may include padding or unwind fragments'
        elif span is None:bound='32-byte leaf window'
        insns=list(md.disasm(pe.get_data(a,b-a),a))
        if not insns:continue
        filename=f'{a:08x}.asm'
        lines=['; '+n for n in names.get(a,[])]+['; RVAs; static evidence, no live allocation counts']
        for ins in insns:
            suffix=''
            if ins.mnemonic=='call':
                try:target=int(ins.op_str,16)
                except ValueError:target=None
                if target in names:suffix=' ; '+' | '.join(names[target])
            for row in xrefs:
                if row['at']==ins.address:suffix+=' ; '+row['text']
            lines.append(f'{ins.address:08x}  {ins.mnemonic:8} {ins.op_str}{suffix}')
        (args.out/filename).write_text('\n'.join(lines)+'\n')
        evidence.append(dict(rva=a,end_rva=b,bound=bound,symbols=names.get(a,[]),file=filename))
    result=dict(schema='gowin-longwire-static-audit-v1',dll=str(args.dll),dll_sha256=hashlib.sha256(data).hexdigest(),
        scope='Read-only PE exports, ASCII diagnostics, validated LEA/call instruction references, and unwind-range disassembly. No original source or live resource exhaustion inferred.',
        pe_named_exports=len(exports),exports=selected,strings=strings,string_xrefs=xrefs,calls=calls,evidence=evidence)
    (args.out/'manifest.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(dict(exports=len(selected),strings=len(strings),string_xrefs=len(xrefs),calls=len(calls),disassemblies=len(evidence))))


if __name__=='__main__':main()
