"""Extract STA binary interfaces and disassembly evidence, not original C++ source."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'binary_audit/python_libs'))
import capstone
import pefile


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dll', type=Path, default=Path('C:/Gowin/Gowin_V1.9.12.04_x64/IDE/bin/gowin.dll'))
    ap.add_argument('--out', type=Path, required=True)
    args = ap.parse_args()
    # pefile defaults to 8192 exports; this DLL has 8275 named exports.
    pe = pefile.PE(str(args.dll), max_symbol_exports=100000)
    named_count = sum(e.name is not None for e in pe.DIRECTORY_ENTRY_EXPORT.symbols)
    if named_count != pe.DIRECTORY_ENTRY_EXPORT.struct.NumberOfNames:
        raise ValueError('Incomplete export parsing; refusing a partial STA inventory')
    exports = {e.name.decode(errors='replace'): e.address for e in pe.DIRECTORY_ENTRY_EXPORT.symbols if e.name}
    relevant = {name: hex(rva) for name,rva in exports.items() if '@STA@@' in name or '@SDCPARSER@@' in name}
    args.out.mkdir(parents=True, exist_ok=True)
    selected = {name:rva for name,rva in exports.items() if any(s in name for s in
        ['?readTimingData@TmMonitor', '?getLutUnitDelay@TmMonitor', '?getDffUnitDelay@TmMonitor', '?getAluUnitDelay@TmMonitor'])}
    # Internal helpers verified for the hash-pinned DLL used in current probes.
    dll_hash = hashlib.sha256(args.dll.read_bytes()).hexdigest()
    if dll_hash == 'bc9472a5a22cf4a94e39e07439f261f3354c668f92ef9abc735e6678bea53b7b':
        selected.update({'internal_read_timing_impl':0x139dbc0,
            'internal_extended_reader':0x148a3e0,'internal_float_row_reader':0x1489a40,
            'internal_relation_reader':0x1489c40,'internal_relation_record_reader':0x1489d70,
            'internal_shared_mapping_reader':0x14897b0,
            'internal_cell_delay_lookup':0x1444780,'internal_ff_arc_dispatch':0x1446fc0})
    md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_64)
    ranges = [(e.struct.BeginAddress,e.struct.EndAddress) for e in pe.DIRECTORY_ENTRY_EXCEPTION]
    evidence = []
    # MSVC unwind ranges can split one source function at try regions.
    verified_ends = {0x148a3e0:0x148ab40,0x1489a40:0x1489c40,
                     0x1489c40:0x1489d70,0x1382af0:0x1382e78,
                     0x1444780:0x1444806}
    for name,rva in selected.items():
        end = next((b for a,b in ranges if a<=rva<b), rva+16)
        if dll_hash == 'bc9472a5a22cf4a94e39e07439f261f3354c668f92ef9abc735e6678bea53b7b':
            end = verified_ends.get(rva,end)
        filename = f'{rva:08x}.asm'
        instructions = '\n'.join(f'{i.address:08x}  {i.mnemonic:8} {i.op_str}'
            for i in md.disasm(pe.get_data(rva,end-rva),rva))
        (args.out/filename).write_text(f'; {name}\n; RVA, not process VA\n{instructions}\n')
        evidence.append(dict(symbol=name,rva=hex(rva),end_rva=hex(end),file=filename))
    manifest = dict(source=str(args.dll),sha256=dll_hash,scope='Export symbols and disassembly; no original source recovered',
        pe_named_exports=named_count,exports=relevant,evidence=evidence)
    (args.out/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    print(f'{len(relevant)} STA/SDC exports; {len(evidence)} function disassemblies')


if __name__ == '__main__':
    main()
