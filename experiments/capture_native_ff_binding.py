"""Observe native FF pin binding on the existing fixed-pin int2float probe.

This is an architecture oracle observation, never an own-P&R result. Debugger
stops on the first non-null physical register binding and emits no bitstream.
"""
import json,os,subprocess,sys,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from gowin_probe import prepare,BIN
from run_abc_reproduction import sha
from capture_runtime_tables import EXPECTED

def main():
    out=ROOT/'results/native_ff_binding_001';prepare(out)
    if sha(BIN/'gowin.dll')!=EXPECTED:raise ValueError('Unreviewed DLL')
    source=ROOT/'results/synthesis_wide_int157_001/lut4/05_netlist/mapped.vg';cst=ROOT/'results/int2float_clock_ablation_001/lut4.cst';sdc=ROOT/'results/fullio_closure_003/int_lut4_local155_edge.sdc'
    (out/'probe.vg').write_bytes(source.read_bytes());(out/'probe.sdc').write_bytes(sdc.read_bytes());(out/'project.cst').write_bytes(cst.read_bytes())
    cmd=out/'cmd.do';cmd.write_text(cmd.read_text()+f'-cst {(out/"project.cst").as_posix()}\n')
    script=out/'capture.cdb';lines=['bu gowin+0x30b1c9','g','r @$t0=@rax','.printf "PHYSICAL_FF %p\\n", @$t0','dw @$t0+0x10 L4']
    for offset in (0x18,0x20,0x28,0x30,0x38):lines.extend([f'.printf "FIELD {offset:x} %p\\n", poi(@$t0+0x{offset:x})',f'dw poi(@$t0+0x{offset:x}) L3'])
    lines.extend(['.echo FF_BINDING_CAPTURED','q']);script.write_text('\n'.join(lines)+'\n')
    python=ROOT/'binary_audit/flow/runtime/bin/python.exe';cdb='C:/Program Files (x86)/Windows Kits/10/Debuggers/x64/cdb.exe';start=time.monotonic()
    with (out/'console.log').open('w') as log:
        cp=subprocess.run([cdb,'-G','-logo',str(out/'snapshot.log'),'-cf',str(script),str(python),str(ROOT/'scripts/gowin_probe.py'),'--worker','pnr','--out',str(out)],cwd=python.parent,env=dict(os.environ,PYTHONHOME=sys.base_prefix),stdout=log,stderr=subprocess.STDOUT,timeout=90)
    text=(out/'snapshot.log').read_text(errors='replace');m=dict(status='captured' if 'FF_BINDING_CAPTURED' in text else 'failed',scope=__doc__,dll_sha256=EXPECTED,source_sha256=sha(source),cst_sha256=sha(cst),snapshot_sha256=sha(out/'snapshot.log'),open_chip_database_used=False,returncode=cp.returncode,wall_seconds=time.monotonic()-start)
    (out/'manifest.json').write_text(json.dumps(m,indent=2));print(json.dumps(m,indent=2))
if __name__=='__main__':main()
