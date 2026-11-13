"""Capture Gowin's initialized route-node names; stop before placement.

No open chip database is read. Offsets are pinned to an audited DLL build.
"""
import argparse,json,os,re,subprocess,sys,time
from pathlib import Path
from gowin_probe import ROOT,BIN,prepare
from run_abc_reproduction import sha
from capture_runtime_tables import EXPECTED

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--out',type=Path,required=True);args=ap.parse_args();out=args.out.resolve()
    if sha(BIN/'gowin.dll')!=EXPECTED:raise ValueError('Unreviewed DLL build')
    prepare(out)
    # InitNodeNameMap entry is after node-name construction, RCX is its this.
    # GetNodeName establishes std::string layout: base+0x18, stride 32;
    # extra names pointer at +0x3220; total uint16 count at +0x341a.
    commands=out/'capture.cdb'
    body=(r'.printf "ROUTE_TABLE %p COUNT %u\n", @$t0, wo(@$t0+0x341a); '
          r'.for (r @$t1=0; @$t1<wo(@$t0+0x341a); r @$t1=@$t1+1) { '
          r'r @$t2=@$t0+0x18+@$t1*0x20; '
          r'.printf "ROUTE_NODE %u %ma\n", @$t1, @$t2; }; '
          r'.printf "ROUTE_CAPTURE_DONE\n"; q')
    commands.write_text('bu gowin+0x48b820\ng\nr @$t0=@rcx\n.writemem "'+(out/'table.bin').as_posix()+'" @$t0 L0x3500\n'+body+'\n')
    debugger=Path('C:/Program Files (x86)/Windows Kits/10/Debuggers/x64/cdb.exe');python=ROOT/'binary_audit/flow/runtime/bin/python.exe'
    start=time.monotonic()
    try:
        with (out/'debugger_console.log').open('w') as log:
            cp=subprocess.run([str(debugger),'-G','-logo',str(out/'snapshot.log'),'-cf',str(commands),str(python),str(ROOT/'scripts/gowin_probe.py'),'--worker','pnr','--out',str(out)],cwd=python.parent,env=dict(os.environ,PYTHONHOME=sys.base_prefix),stdout=log,stderr=subprocess.STDOUT,timeout=90)
    except subprocess.TimeoutExpired:
        (out/'manifest.json').write_text(json.dumps(dict(status='failed',reason='Capture timed out',dll_sha256=EXPECTED,open_chip_database_used=False),indent=2))
        raise
    text=(out/'snapshot.log').read_text(errors='replace');rows={int(i):name for i,name in re.findall(r'^ROUTE_NODE (\d+) ([^\r\n]+)',text,re.M)}
    counts=re.findall(r'^ROUTE_TABLE \S+ COUNT (\d+)',text,re.M)
    import struct
    raw=(out/'table.bin').read_bytes() if (out/'table.bin').exists() else b''
    inline_valid=len(raw)>=0x3500 and len(rows)<=320 and all(struct.unpack_from('<Q',raw,0x18+i*32+24)[0]<16 for i in rows)
    ok=bool(counts) and len(rows)==int(counts[-1]) and 'ROUTE_CAPTURE_DONE' in text and inline_valid
    result=dict(status='captured' if ok else 'failed',source='Installed native Gowin DLL runtime only',dll_sha256=EXPECTED,
        debugger_returncode=cp.returncode,node_count=len(rows),nodes=rows,wall_seconds=time.monotonic()-start,
        snapshot_sha256=sha(out/'snapshot.log'),commands_sha256=sha(commands),placement_executed='Running placement' in text,open_chip_database_used=False)
    (out/'manifest.json').write_text(json.dumps(result,indent=2));print({k:v for k,v in result.items() if k!='nodes'})
    if not ok:raise RuntimeError('Incomplete route-node capture; inspect debugger log')
if __name__=='__main__':main()
