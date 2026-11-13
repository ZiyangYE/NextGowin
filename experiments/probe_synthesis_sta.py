"""Ask the native synthesis report engine to analyze an own mapped netlist.

Read-only timing diagnostic. The optional mapped-analysis initialization calls
native `map -noopt` internally; no resulting netlist is exported or used as a
synthesis seed. This flag must not be described as pure standalone STA.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from gowin_probe import DEVICE,PART,BIN
from run_abc_reproduction import sha

ap=argparse.ArgumentParser();ap.add_argument('source',type=Path);ap.add_argument('--sdc',type=Path,required=True)
ap.add_argument('--out',type=Path,required=True);ap.add_argument('--top',default='top')
ap.add_argument('--profile',type=Path)
ap.add_argument('--initialize-mapped-analysis',action='store_true')
ap.add_argument('--timing-driven-initialization',action='store_true');args=ap.parse_args()
out=args.out.resolve();out.mkdir(parents=True,exist_ok=False);source=args.source.resolve();sdc=args.sdc.resolve()
commands=[f'dev -p {DEVICE} -pn {PART}',f'extract -i {{{source.as_posix()}}} -e {args.top} -o {{{out.as_posix()}/elaborated.v}}',
          f'sdc -i {{{sdc.as_posix()}}}','cvt',*(['map -noopt'+(' -t' if args.timing_driven_initialization else '')] if args.initialize_mapped_analysis else []),
          f'report -o {{{out.as_posix()}/timing.rpt}} -r -l -t 25']
(out/'synth.tcl').write_text('\n'.join(commands)+'\n')
start=time.perf_counter();binary=ROOT/'binary_audit/flow/runtime/bin/python.exe';state=dict(status='running',
    scope=__doc__,native_mapping_called=args.initialize_mapped_analysis,input_sha256=sha(source),sdc_sha256=sha(sdc),synthesis_dll_sha256=sha(BIN/'gwsyn.dll'),commands=commands,
    usable_as_original_constraint_oracle=False,
    constraint_scope='Unverified until the emitted clock table and exception behavior pass a separate audit; a successful sdc command alone is insufficient.')
try:
    with (out/'runner.log').open('w') as log:
        cp=subprocess.run([str(binary),str(ROOT/'scripts/gowin_probe.py'),'--worker','synth','--out',str(out)],
            cwd=binary.parent,env=dict(os.environ,PYTHONHOME=sys.base_prefix),stdout=log,stderr=subprocess.STDOUT,timeout=90)
    state.update(status='diagnostic_complete' if cp.returncode==0 else 'failed',returncode=cp.returncode,
                 input_unchanged=sha(source)==state['input_sha256'])
    if cp.returncode==0 and args.profile:
        with (out/'audit_runner.log').open('w') as log:
            subprocess.run([sys.executable,str(ROOT/'experiments/audit_synthesis_sta_constraints.py'),str(out),
                '--sdc',str(sdc),'--profile',str(args.profile.resolve())],stdout=log,stderr=subprocess.STDOUT,check=True,timeout=10)
        checked=json.loads((out/'constraint_audit.json').read_text())
        state['constraint_audit_sha256']=sha(out/'constraint_audit.json')
        state['usable_as_original_constraint_oracle']=checked['usable_as_original_constraint_oracle']
except Exception as error:state.update(status='failed',error=repr(error))
state['wall_seconds']=time.perf_counter()-start
(out/'manifest.json').write_text(json.dumps(state,indent=2)+'\n');print(state)
if state['status']=='failed':raise SystemExit(1)
