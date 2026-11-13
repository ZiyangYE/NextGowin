"""Show the synthesis DLL's static dependency on the common native STA module."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'binary_audit/python_libs'))
import pefile


def audit(directory):
    syn_path=directory/'gwsyn.dll';sta_path=directory/'gowin.dll'
    syn=pefile.PE(str(syn_path));sta=pefile.PE(str(sta_path),max_symbol_exports=100000)
    named_count=sum(entry.name is not None for entry in sta.DIRECTORY_ENTRY_EXPORT.symbols)
    if named_count != sta.DIRECTORY_ENTRY_EXPORT.struct.NumberOfNames:
        raise ValueError('Incomplete shared DLL export parsing')
    exports={entry.name.decode():entry.address for entry in sta.DIRECTORY_ENTRY_EXPORT.symbols if entry.name}
    dependency=next(item for item in syn.DIRECTORY_ENTRY_IMPORT if item.dll.lower()==b'gowin.dll')
    imports=[]
    for entry in dependency.imports:
        name=entry.name.decode() if entry.name else '#'+str(entry.ordinal)
        if '@STA@@' not in name and '@SDCPARSER@@' not in name:continue
        if name not in exports:raise ValueError('Synthesis STA import not resolved by the selected DLL')
        imports.append(dict(symbol=name,import_iat_rva=hex(entry.address-syn.OPTIONAL_HEADER.ImageBase),
                            resolved_gowin_export_rva=hex(exports[name])))
    if not imports:raise ValueError('No synthesis dependency on shared STA found')
    return dict(scope='Static PE import/export evidence; no claim that every imported method runs during each mapping command',
        synthesis=dict(path=str(syn_path),sha256=hashlib.sha256(syn_path.read_bytes()).hexdigest()),
        shared_sta=dict(path=str(sta_path),sha256=hashlib.sha256(sta_path.read_bytes()).hexdigest(),named_exports=named_count),
        total_gowin_imports=len(dependency.imports),sta_sdc_imports=imports)


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--bin',type=Path,default=Path('C:/Gowin/Gowin_V1.9.12.04_x64/IDE/bin'))
    ap.add_argument('--out',type=Path,required=True);args=ap.parse_args()
    result=audit(args.bin);args.out.parent.mkdir(parents=True,exist_ok=True)
    args.out.write_text(json.dumps(result,indent=2)+'\n')
    print(f'{result["total_gowin_imports"]} imports from gowin.dll; {len(result["sta_sdc_imports"])} resolved STA/SDC methods')


if __name__=='__main__':main()
