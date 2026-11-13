"""Run the official Gowin Tcl synthesis flow as an explicit comparison baseline."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from gowin_probe import ROOT,BIN,PART,DEVICE
from run_abc_reproduction import sha


def synthesize(source,out,top='top',frequency_mhz=100.0,timeout=300,sdc=None):
    out=out.resolve();out.mkdir(parents=True,exist_ok=False);source=source.resolve()
    if any(c in str(source) for c in '{}\r\n'):raise ValueError('Unsupported Tcl source path')
    constraints=out/'clock.sdc'
    constraints.write_bytes(Path(sdc).read_bytes() if sdc else f'create_clock -name clk -period {1000/frequency_mhz:.9f} [get_ports {{clk}}]\n'.encode())
    commands=[f'dev -p {DEVICE} -pn {PART}',
              f'extract -i {{{source.as_posix()}}} -e {top} -o {{{out.as_posix()}/elaborated.v}}',
              f'sdc -i {{{constraints.as_posix()}}}',
              'cvt','dio -novgopt','infer -ram -dsp','ato','dio','map -t','dio -divfanout',
              f'output -v {{{out.as_posix()}/mapped.vg}}']
    script=out/'synth.tcl';script.write_text('\n'.join(commands)+'\n')
    result=dict(status='running',flow='Direct pinned Gowin synthesis DLL: elaboration, SDC, optimization, resource inference, timing-first map and fanout optimization. Baseline only.',
                source=str(source),source_sha256=sha(source),frequency_mhz=frequency_mhz,part=PART,
                synthesis_dll_sha256=sha(BIN/'gwsyn.dll'),timing_dll_sha256=sha(BIN/'gowin.dll'),sdc_sha256=sha(constraints),script_sha256=sha(script))
    manifest=out/'manifest.json';manifest.write_text(json.dumps(result,indent=2)+'\n');started=time.perf_counter()
    with (out/'synthesis.log').open('w') as log:
        runtime=ROOT/'binary_audit/flow/runtime/bin/python.exe'
        cp=subprocess.run([str(runtime),str(ROOT/'scripts/gowin_probe.py'),'--worker','synth','--out',str(out)],
                          cwd=runtime.parent,env=dict(os.environ,PYTHONHOME=sys.base_prefix),stdout=log,stderr=subprocess.STDOUT,timeout=timeout)
    result.update(returncode=cp.returncode,wall_seconds=time.perf_counter()-started)
    outputs=list(out.glob('mapped.vg'))
    result['outputs']={str(p.relative_to(out)):sha(p) for p in outputs}
    result['status']='complete' if cp.returncode==0 and len(outputs)==1 else 'failed'
    manifest.write_text(json.dumps(result,indent=2)+'\n')
    if result['status']!='complete':raise RuntimeError('Native synthesis failed: '+str(out/'synthesis.log'))
    return outputs[0],result


def main():
    ap=argparse.ArgumentParser();ap.add_argument('input',type=Path);ap.add_argument('--out',type=Path,required=True)
    ap.add_argument('--frequency-mhz',type=float,default=100);args=ap.parse_args()
    path,record=synthesize(args.input,args.out,frequency_mhz=args.frequency_mhz)
    print(json.dumps(dict(status=record['status'],netlist=str(path))))


if __name__=='__main__':main()
