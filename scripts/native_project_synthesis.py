"""Re-run a checked frontend job through the full native synthesis baseline.

The frontend's actual sources/include snapshot are re-elaborated; neither its
elaborated netlist nor any own-mapped logic is supplied to the vendor mapper.
"""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from gowin_probe import ROOT,BIN
from run_abc_reproduction import sha


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--frontend',type=Path,required=True)
    ap.add_argument('--sdc',type=Path,required=True);ap.add_argument('--out',type=Path,required=True)
    args=ap.parse_args();frontend=args.frontend.resolve();out=args.out.resolve();out.mkdir(parents=True,exist_ok=False)
    meta=json.loads((frontend/'manifest.json').read_text())
    if meta['status']!='complete' or meta['synthesis_dll_sha256']!=sha(BIN/'gwsyn.dll'):raise ValueError('Frontend/tool mismatch')
    if any(sha(Path(p))!=digest for p,digest in meta['inputs'].items()):raise ValueError('Original RTL/project changed')
    for relative,digest in meta['staged_include_files'].items():
        if sha(frontend/'includes'/relative)!=digest:raise ValueError('Staged include snapshot changed')
    commands=list(meta['commands'])
    # Retain every input/language/GAO/include argument; only redirect the
    # elaborated-output file into this fresh baseline directory.
    old=(frontend/'elaborated.v').as_posix();new=(out/'elaborated.v').as_posix()
    commands=[c.replace(old,new) for c in commands]
    if not any(new in c for c in commands):raise ValueError('Unknown frontend export command format')
    sdc=out/'project.sdc';sdc.write_bytes(args.sdc.read_bytes())
    commands += [f'sdc -i {{{sdc.as_posix()}}}','cvt','dio -novgopt','infer -ram -dsp','ato','dio','map -t','dio -divfanout',f'output -v {{{out.as_posix()}/mapped.vg}}']
    (out/'synth.tcl').write_text('\n'.join(commands)+'\n')
    result=dict(status='running',source='Full native Gowin synthesis from the same checked RTL frontend inputs; no own netlist supplied',
                frontend_manifest_sha256=sha(frontend/'manifest.json'),rtl_input_sha256=meta['inputs'],
                include_sha256=meta['staged_include_files'],wrapper_sha256=sha(frontend/'frontend_inputs.v'),
                synthesis_dll_sha256=sha(BIN/'gwsyn.dll'),timing_dll_sha256=sha(BIN/'gowin.dll'),
                sdc_sha256=sha(sdc),script_sha256=sha(out/'synth.tcl'))
    def save():(out/'manifest.json').write_text(json.dumps(result,indent=2)+'\n')
    save();start=time.perf_counter();runtime=ROOT/'binary_audit/flow/runtime/bin/python.exe'
    try:
        with (out/'synthesis.log').open('w') as log:
            cp=subprocess.run([str(runtime),str(ROOT/'scripts/gowin_probe.py'),'--worker','synth','--out',str(out)],cwd=runtime.parent,
                              env=dict(os.environ,PYTHONHOME=sys.base_prefix),stdout=log,stderr=subprocess.STDOUT,timeout=1800)
        result.update(returncode=cp.returncode,wall_seconds=time.perf_counter()-start)
        if cp.returncode or not (out/'mapped.vg').exists():raise RuntimeError('Full native synthesis failed')
        if any(sha(Path(p))!=digest for p,digest in meta['inputs'].items()):raise ValueError('RTL inputs changed during synthesis')
        result.update(status='complete',netlist_sha256=sha(out/'mapped.vg'));save()
    except Exception as e:
        result.update(status='failed',error=repr(e),wall_seconds=time.perf_counter()-start);save();raise
    print(json.dumps(dict(status=result['status'],wall_seconds=result['wall_seconds'])))


if __name__=='__main__':main()
