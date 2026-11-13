import sys,struct,re,json
from pathlib import Path
sys.path.insert(0,str(Path('binary_audit/python_libs').resolve()))
import pefile
p=pefile.PE('C:/Gowin/Gowin_V1.9.12.04_x64/IDE/bin/gwsyn.dll'); b=p.__data__; targets={}
for needle in [b'Trying to combine GAO',b'[5%] Running netlist conversion']:
 pos=b.find(needle); r=p.get_rva_from_offset(pos);targets[r]=needle.decode();print('STRING',hex(r),b[pos:b.find(b'\0',pos)])
funcs=[(e.struct.BeginAddress,e.struct.EndAddress) for e in p.DIRECTORY_ENTRY_EXCEPTION]
for sec in p.sections:
 if not sec.Characteristics & 0x20000000:continue
 data=sec.get_data()
 for m in re.finditer(rb'[\x48-\x4f]\x8d[\x05\x0d\x15\x1d\x25\x2d\x35\x3d]',data):
  off=m.start(); rva=sec.VirtualAddress+off;t=rva+7+struct.unpack_from('<i',data,off+3)[0]
  if t in targets:
   fn=next((x for x in funcs if x[0]<=rva<x[1]),None);print('XREF',targets[t],hex(rva),'FUNCTION',tuple(hex(x) for x in fn) if fn else None)
