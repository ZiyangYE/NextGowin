"""Trace row IDs actually requested by the hash-pinned native FF timing path."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from gowin_probe import ROOT,BIN,prepare
from capture_runtime_tables import EXPECTED


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--out',type=Path,required=True)
    args=ap.parse_args()
    dll=BIN/'gowin.dll'
    if hashlib.sha256(dll.read_bytes()).hexdigest()!=EXPECTED:
        raise RuntimeError('DLL hash mismatch; query addresses require another audit')
    out=args.out.resolve();prepare(out)
    commands=out/'trace.cdb'
    query=out/'query.cdb'
    query.write_text('.if (@r8==@$t0) { .if (@r9<0x20) { .if ((@$t1 & (1<<@r9))==0) { '
        'r @$t1=@$t1 | (1<<@r9); .printf "FF_QUERY row=%x selector=%x caller_rva=%p src_id=%x dst_id=%x arg6=%x arg7=%x\\n", '
        '@r9, dwo(@rsp+0x28), poi(@rsp)-gowin, @$t2, @$t3, @$t4, @$t5; kn 5; }; }; }; '
        '.if ((@$t1 & 0x1000050)!=0x1000050) { gc; }\n')
    commands.write_text('bu gowin+0x139f8b4\ng\n'
        'r @$t0=poi(poi(poi(@r14+0x38)+8)+0x18)\n'
        'r @$t1=0\n'
        'bc *\n'
        'bu gowin+0x1446fc0 ".if ((@r8!=0) & (@r9!=0)) { '
        'r @$t2=dwo(@r8+0x48); r @$t3=dwo(@r9+0x48); '
        'r @$t4=by(@rsp+0x30); r @$t5=by(@rsp+0x38); }; gc"\n'
        f'bu gowin+0x1444780 "$$><{query.as_posix()}"\n'
        'g\n.echo FF_TRACE_COMPLETE\nq\n')
    log=out/'trace.log'
    python=ROOT/'binary_audit/flow/runtime/bin/python.exe'
    cdb='C:/Program Files (x86)/Windows Kits/10/Debuggers/x64/cdb.exe'
    started=time.perf_counter()
    with (out/'debugger.stdout').open('w') as stdout:
        try:
            cp=subprocess.run([cdb,'-G','-logo',str(log),'-cf',str(commands),str(python),
                str(ROOT/'scripts/gowin_probe.py'),'--worker','pnr','--out',str(out)],
                cwd=python.parent,env=dict(os.environ,PYTHONHOME=sys.base_prefix),
                stdout=stdout,stderr=subprocess.STDOUT,timeout=60)
            code=cp.returncode
        except subprocess.TimeoutExpired:
            code='timeout'
    text=log.read_text(errors='replace')
    rows=[dict(row=int(a,16),selector=int(b,16),caller_rva=c,
               source_pin_id=int(d,16),destination_pin_id=int(e,16),arg6=int(f,16),arg7=int(g,16)) for a,b,c,d,e,f,g in
        re.findall(r'^FF_QUERY row=([0-9a-f]+) selector=([0-9a-f]+) caller_rva=([0-9a-f`]+) src_id=([0-9a-f]+) dst_id=([0-9a-f]+) arg6=([0-9a-f]+) arg7=([0-9a-f]+)',text,re.M)]
    complete=code==0 and '\nFF_TRACE_COMPLETE\n' in text and {4,6,24}<={r['row'] for r in rows}
    result=dict(scope='FF row query capture only; debugger stops before placement. Not a completed P&R run. Row semantics require separate evidence.',
        dll_sha256=EXPECTED,returncode=code,wall_seconds=time.perf_counter()-started,
        command_sha256=hashlib.sha256(commands.read_bytes()).hexdigest(),queries=rows,
        query_script_sha256=hashlib.sha256(query.read_bytes()).hexdigest(),
        report_present=(out/'probe.tr').exists(),status='complete' if complete else 'failed')
    (out/'manifest.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result,indent=2))
    if result['status']!='complete':raise RuntimeError('FF trace failed or no FF queries observed')


if __name__=='__main__':main()
