"""Bounded native long-wire probe on an artificial high-fanout design.

Uses software breakpoints for observation; no installed file is modified.
This is not a timing/functional acceptance run of the user's design.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from gowin_probe import ROOT,BIN,prepare
from capture_runtime_tables import EXPECTED


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--out',type=Path,required=True)
    ap.add_argument('--registers',type=int,default=128)
    ap.add_argument('--timeout',type=int,default=300)
    ap.add_argument('--finish-routing',action='store_true',help='Attempt routing within the diagnostic limit; default stops at the observed 138K graph entry')
    args=ap.parse_args()
    if not 32<=args.registers<=4096:ap.error('Use 32..4096 registers for this diagnostic')
    if not 1<=args.timeout<=300:ap.error('Diagnostic limit is 300 seconds')
    if hashlib.sha256((BIN/'gowin.dll').read_bytes()).hexdigest()!=EXPECTED:
        raise ValueError('Probe RVAs require the audited DLL')
    out=args.out.resolve();prepare(out)
    n=args.registers
    lines=['module top(clk,ce,rst,a,q);','input clk,ce,rst,a; output q;',f'wire ck,en,reset,ai,vcc,gnd; wire [{n-1}:0] state;',
        'IBUF ib_clk(.I(clk),.O(ck));','IBUF ib_ce(.I(ce),.O(en));',
        'IBUF ib_rst(.I(rst),.O(reset));','IBUF ib_a(.I(a),.O(ai));',
        'VCC vc(.V(vcc));','GND gc(.G(gnd));','GSR gs(.GSRI(vcc));']
    for i in range(n):
        lines.append(f'DFFRE ff_{i}(.D('+('ai' if i==0 else f'state[{i-1}]')+f'),.CLK(ck),.CE(en),.RESET(reset),.Q(state[{i}]));')
    lines+= [f'OBUF ob_q(.I(state[{n-1}]),.O(q));','endmodule']
    (out/'probe.vg').write_text('\n'.join(lines)+'\n')
    (out/'probe.sdc').write_text('create_clock -name probe_clk -period 20 [get_ports {clk}]\n')
    cmd=out/'cmd.do';cmd.write_text('-D9\n'+cmd.read_text().replace('-place_option 3','-place_option 0'))
    trace=out/'trace.cdb'
    graph_action='gc' if args.finish_routing else 'kn 10; q'
    trace.write_text('bu gowin+0x387640 ".echo LW_BUILD_5AT138; '+graph_action+'"\n'
        'bu gowin+0xb316d0 ".echo LW_OLD_ASSIGN; gc"\n'
        'bu gowin+0xb32540 ".echo LW_OLD_ASSIGN_BLOCK; gc"\n'
        'bu gowin+0xb32d40 ".echo LW_OLD_ASSIGN_BLOCK2; gc"\n'
        'bu gowin+0x107efa0 ".echo LW_ROUTER_ENTRY; dd poi(@rcx+0x20)+0x258 L1; gc"\n'
        'bu gowin+0x1097f60 ".echo LW_ROUTER_RETURN; r al; gc"\n'
        'bu gowin+0x10673c0 ".echo LW_DEBUG_REPORT; gc"\n'
        'g\n.echo LW_PROBE_END\nq\n')
    python=ROOT/'binary_audit/flow/runtime/bin/python.exe'
    cdb='C:/Program Files (x86)/Windows Kits/10/Debuggers/x64/cdb.exe'
    start=time.perf_counter()
    with (out/'debugger.stdout').open('w') as log:
        try:
            cp=subprocess.run([cdb,'-G','-logo',str(out/'trace.log'),'-cf',str(trace),str(python),
                str(ROOT/'scripts/gowin_probe.py'),'--worker','pnr','--out',str(out)],cwd=python.parent,
                env=dict(os.environ,PYTHONHOME=sys.base_prefix),stdout=log,stderr=subprocess.STDOUT,timeout=args.timeout)
            code=cp.returncode
        except subprocess.TimeoutExpired:code='timeout'
    text=(out/'trace.log').read_text(errors='replace') if (out/'trace.log').exists() else ''
    result=dict(scope='Artificial high-fanout native routing diagnostic; not FPGAFly acceptance',
        dll_sha256=EXPECTED,registers=n,returncode=code,wall_seconds=time.perf_counter()-start,
        trace_sha256=hashlib.sha256(trace.read_bytes()).hexdigest(),
        input_sha256=hashlib.sha256((out/'probe.vg').read_bytes()).hexdigest(),
        native_report_present=(out/'probe.tr').exists(),
        markers=[line for line in text.splitlines() if line.startswith('LW_')])
    observed='LW_BUILD_5AT138' in result['markers']
    ok=code==0 and (result['native_report_present'] if args.finish_routing else observed)
    result.update(status='complete' if ok else 'failed',finish_routing_requested=args.finish_routing,
        stopped_at_graph_entry=observed and not args.finish_routing)
    (out/'manifest.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result,indent=2))
    if not ok:raise RuntimeError('Diagnostic probe did not reach its requested observation/completion point')


if __name__=='__main__':main()
