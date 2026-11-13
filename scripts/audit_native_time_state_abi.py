"""Reproduce read-only timing-state ABI disassembly, not original C++ source."""
import hashlib,json,sys,argparse
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'binary_audit/python_libs'))
import pefile,capstone
from workspace_budget import reserve
from gowin_probe import BIN


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--out',type=Path,required=True);a=p.parse_args();reserve(ROOT,1_000_000)
    path=BIN/'gowin.dll';h=hashlib.sha256(path.read_bytes()).hexdigest()
    if h!='bc9472a5a22cf4a94e39e07439f261f3354c668f92ef9abc735e6678bea53b7b':raise ValueError('Unreviewed DLL hash')
    pe=pefile.PE(str(path),max_symbol_exports=100000);md=capstone.Cs(capstone.CS_ARCH_X86,capstone.CS_MODE_64);a.out.mkdir(exist_ok=False)
    ranges=[('selected_arrival',0x137f090,0x137f1fa),('selected_slack',0x13bbda0,0x13bbea0),('pair_index',0x141a4d0,0x141a552),('pair_arrival',0x141a560,0x141a5b3),('pair_slack',0x141a9c0,0x141aa27)];evidence=[]
    for name,start,end in ranges:
        text='; RVA disassembly; no recovered source\n'+'\n'.join(f'{i.address:08x} {i.mnemonic} {i.op_str}' for i in md.disasm(pe.get_data(start,end-start),start))+'\n';f=a.out/(name+'.asm');f.write_text(text);evidence.append(dict(name=name,start_rva=hex(start),end_rva=hex(end),sha256=hashlib.sha256(f.read_bytes()).hexdigest()))
    meta=dict(scope=__doc__,dll_sha256=h,evidence=evidence,observed_layout=dict(pin_vertex=0x20,vertex_group_vector=0x28,group_kind=8,group_state_vector=0x10,state_flags=0x18,state_pairs=0x10,pair_bytes=8),semantic_promotion='Only with full report, native selected-slack and internal propagation validation; no general unpinned ABI claim')
    (a.out/'manifest.json').write_text(json.dumps(meta,indent=2));print('Timing-state ABI evidence saved',a.out)


if __name__=='__main__':main()
