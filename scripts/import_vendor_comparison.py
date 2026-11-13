"""Import a vendor mapped netlist for diagnostics, with a common opaque DDR cut.

The derived input omits protected text and cannot be used for implementation.
No decryption, synthesis, optimization, or P&R is performed.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
from yosys_runtime import run,quote


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('source',type=Path);ap.add_argument('--reference',type=Path,required=True)
    ap.add_argument('--vendor-library',type=Path,required=True);ap.add_argument('--out',type=Path,required=True)
    args=ap.parse_args();out=args.out.resolve();out.mkdir(parents=True,exist_ok=False)
    raw=args.source.read_bytes();text=raw.decode('utf-8')
    protected=re.findall(r'`pragma protect begin_protected.*?`pragma protect end_protected',text,re.S)
    if len(protected)!=1:raise ValueError('Expected one protected DDR payload')
    clean=text.replace(protected[0],'// Protected DDR payload omitted for diagnostics; D3_320 is opaque.\n')
    (out/'readable.vg').write_text(clean,encoding='utf-8')
    reference=json.loads(args.reference.read_bytes());module=reference['modules']['GW_JTAG']
    # The installed Yosys library lacks this primitive. Reuse only the known
    # native port directions/widths from the new flow's exported interface.
    if module.get('cells'):raise ValueError('GW_JTAG reference must be opaque')
    lines=['(* blackbox = 1 *) module GW_JTAG('+','.join(module['ports'])+');']
    for name,row in module['ports'].items():
        width=len(row['bits']);packed=f'[{width-1}:0] ' if width>1 else ''
        lines.append(row['direction']+' '+packed+name+';')
    lines.append('endmodule');(out/'jtag.v').write_text('\n'.join(lines)+'\n')
    invocation=run(['read_verilog -lib +/gowin/cells_sim.v','read_verilog -lib +/gowin/cells_xtra_gw5a.v',
        'read_verilog -overwrite -lib '+quote(args.vendor_library),
        'read_verilog -lib '+quote(out/'jtag.v'),'read_verilog '+quote(out/'readable.vg'),
        'blackbox D3_320','blackbox =A:whitebox','hierarchy -check -top thefly_top',
        'write_json '+quote(out/'baseline.json')],out,'import',timeout=120)
    record=dict(scope='Structural diagnostic only. Protected DDR omitted; entire D3_320 wrapper is a blackbox for a common comparison boundary. Not a deployable netlist or equivalence proof.',
        source=str(args.source.resolve()),source_sha256=hashlib.sha256(raw).hexdigest(),
        protected_sha256=hashlib.sha256(protected[0].encode()).hexdigest(),
        reference_sha256=hashlib.sha256(args.reference.read_bytes()).hexdigest(),
        output_sha256=hashlib.sha256((out/'baseline.json').read_bytes()).hexdigest(),invocation=invocation)
    (out/'manifest.json').write_text(json.dumps(record,indent=2)+'\n');print(json.dumps(record,indent=2))


if __name__=='__main__':main()
