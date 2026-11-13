"""Validate LUT INIT readback, then run Gowin P&R without synthesis."""
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from gowin_blif import read_blif,emit,readback
from gowin_probe import BIN,ROOT,DEVICE,PART
from run_abc_reproduction import cec
from gowin_report import summaries


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('input',type=Path)
    ap.add_argument('--reference',type=Path,help='Original AIG for independent readback verification')
    ap.add_argument('--out',type=Path,required=True)
    ap.add_argument('--timeout',type=int,default=180)
    ap.add_argument('--hold-uncertainty-ns',type=float,default=0.0)
    args=ap.parse_args()
    if not math.isfinite(args.hold_uncertainty_ns) or args.hold_uncertainty_ns<0:
        ap.error('Hold uncertainty must be finite and not negative')
    out=args.out.resolve();out.mkdir(parents=True,exist_ok=False)
    network=read_blif(args.input)
    vg=out/'mapped.vg';vg.write_text(emit(network))
    blif=out/'readback.blif';blif.write_text(readback(vg.read_text()))
    manifest=dict(scope='Externally mapped LUT4 network with registered boundaries; P&R only, no Gowin synthesis',
        input_sha256=sha(args.input),mapped_sha256_before=sha(vg),part=PART,
        inputs=len(network['inputs']),outputs=len(network['outputs']),
        gowin_dll_sha256=sha(BIN/'gowin.dll'),status='verifying')
    manifest['hold_uncertainty_ns']=args.hold_uncertainty_ns
    def save():
        (out/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    manifest['readback_cec']=cec(ROOT/'build/abc/_TEST/abc.exe',
        (args.reference or args.input).resolve(),blif,out/'readback_cec.log',args.timeout)
    save()
    if not manifest['readback_cec']['equivalent']:
        raise RuntimeError('INIT readback CEC failed or unproven')
    sdc=out/'probe.sdc'
    sdc.write_text('create_clock -name probe_clk -period 10 [get_ports {clk}]\n'
        'set_input_delay -max 0 -clock probe_clk [get_ports {a[*]}]\n'
        'set_input_delay -min 0 -clock probe_clk [get_ports {a[*]}]\n'
        'set_output_delay -max 0 -clock probe_clk [get_ports {q[*]}]\n'
        'set_output_delay -min 0 -clock probe_clk [get_ports {q[*]}]\n')
    if args.hold_uncertainty_ns:
        with sdc.open('a') as stream:
            stream.write(f'set_clock_uncertainty -hold {args.hold_uncertainty_ns:.6f} -from [get_clocks {{probe_clk}}] -to [get_clocks {{probe_clk}}]\n')
    commands=[f'-d {vg.as_posix()}',f'-p {DEVICE}',f'-pn {PART}',
        '-tr','-timing','-place_option 3','-route_option 2',
        '-correct_hold 1','-global_freq 100.000',
        '-ireg_not_in_iob','-oreg_not_in_iob','-ioreg_not_in_iob',
        f'-sdc {sdc.as_posix()}',f'-o {out.as_posix()}/mapped.vo']
    (out/'cmd.do').write_text('\n'.join(commands)+'\n')
    python=ROOT/'binary_audit/flow/runtime/bin/python.exe'
    manifest['status']='pnr_running';save()
    started=time.perf_counter()
    with (out/'pnr.log').open('w') as log:
        try:
            cp=subprocess.run([str(python),str(ROOT/'scripts/gowin_probe.py'),'--worker','pnr','--out',str(out)],
                cwd=python.parent,env=dict(os.environ,PYTHONHOME=sys.base_prefix),
                stdout=log,stderr=subprocess.STDOUT,timeout=args.timeout)
            manifest['returncode']=cp.returncode
        except subprocess.TimeoutExpired:
            manifest['status']='timeout';manifest['pnr_wall_seconds']=time.perf_counter()-started;save()
            raise
    manifest['pnr_wall_seconds']=time.perf_counter()-started
    manifest['mapped_sha256_after']=sha(vg)
    report=out/'mapped.tr';vo=out/'mapped.vo'
    ok=(cp.returncode==0 and report.exists() and vo.exists() and
        manifest['mapped_sha256_before']==manifest['mapped_sha256_after'] and
        not re.search(r'^ERROR\s*\(', (out/'pnr.log').read_text(errors='replace'),re.M))
    manifest['status']='complete' if ok else 'failed';save()
    if not ok:
        raise RuntimeError('P&R failed or outputs missing')
    text=report.read_text(errors='replace')
    manifest['report_sha256']=sha(report)
    manifest['post_pnr_sha256']=sha(vo)
    manifest.update(summaries(text,args.hold_uncertainty_ns))
    save();print(json.dumps(manifest,indent=2))


if __name__=='__main__':
    main()
