"""P&R an existing structural project netlist with unchanged board constraints."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from gowin_probe import ROOT,BIN,PART,DEVICE
from capture_runtime_tables import EXPECTED


def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('input',type=Path);ap.add_argument('--sdc',type=Path,required=True)
    ap.add_argument('--cst',type=Path);ap.add_argument('--out',type=Path,required=True)
    ap.add_argument('--timeout',type=int,default=1800)
    ap.add_argument('--bitstream',action='store_true',help='Also request configuration output; not needed for P&R timing comparison')
    ap.add_argument('--co-place-io-registers',action='store_true',help='Labelled vendor calibration: enable documented native input/output register co-placement')
    ap.add_argument('--rao',type=Path,help='Paired RTL GAO configuration; preserve its CRC and request RTL GAO output')
    ap.add_argument('--device-config',type=Path)
    iob=ap.add_mutually_exclusive_group()
    iob.add_argument('--no-iob-registers',action='store_true',help='Keep explicit external-flow FFs in core logic')
    iob.add_argument('--output-iob-registers',action='store_true',help='Allow output FF packing only; keep input and bidirectional FFs in core logic')
    args=ap.parse_args()
    if sha(BIN/'gowin.dll')!=EXPECTED:raise ValueError('P&R DLL version differs from the audited version')
    out=args.out.resolve();out.mkdir(parents=True,exist_ok=False)
    netlist=out/'mapped.vg';netlist.write_bytes(args.input.read_bytes())
    sdc=out/'project.sdc';sdc.write_bytes(args.sdc.read_bytes())
    cst=out/'project.cst' if args.cst else None
    if cst:cst.write_bytes(args.cst.read_bytes())
    commands=[f'-d {netlist.as_posix()}',f'-p {DEVICE}',f'-pn {PART}',
        '-tr','-timing','-place_option 3','-route_option 2','-route_maxfan 23',
        '-correct_hold 1',f'-sdc {sdc.as_posix()}',
        f'-o {out.as_posix()}/mapped.vo']
    if cst:commands.insert(-1,f'-cst {cst.as_posix()}')
    if args.co_place_io_registers:commands.insert(-1,'-co-place_io_registers 1')
    if args.no_iob_registers:
        commands[-1:-1]=['-ireg_not_in_iob','-oreg_not_in_iob','-ioreg_not_in_iob']
    elif args.output_iob_registers:
        commands[-1:-1]=['-ireg_not_in_iob','-ioreg_not_in_iob']
    if args.device_config:
        cfg=out/'device.cfg';cfg.write_bytes(args.device_config.read_bytes())
        commands.insert(-1,f'-cfg {cfg.as_posix()}')
    gao_id=None
    if args.rao:
        rao=out/'debug.rao';rao.write_bytes(args.rao.read_bytes())
        gao_id=ET.parse(rao).findtext('.//GAO_ID')
        if not gao_id or not re.fullmatch('[01]{16}',gao_id):raise ValueError('Missing/invalid RTL GAO ID')
        commands[-1:-1]=['-rtl_gao',f'-gao_crc {gao_id}']
    if args.bitstream:commands.insert(3,'-bit')
    (out/'cmd.do').write_text('\n'.join(commands)+'\n')
    result=dict(scope='Direct P&R of externally supplied full project netlist. No synthesis or board programming.',
        status='pnr_running',part=PART,dll_sha256=EXPECTED,input_sha256=sha(args.input),
        netlist_sha256_before=sha(netlist),sdc_sha256=sha(sdc),cst_sha256=sha(cst) if cst else None,timeout=args.timeout,
        gao_id=gao_id,rao_sha256=sha(rao) if args.rao else None,
        bitstream_requested=args.bitstream,co_place_io_registers=args.co_place_io_registers,runner_sha256=sha(Path(__file__)))
    def save():(out/'manifest.json').write_text(json.dumps(result,indent=2)+'\n')
    save();start=time.perf_counter();python=ROOT/'binary_audit/flow/runtime/bin/python.exe'
    with (out/'pnr.log').open('w') as log:
        try:
            cp=subprocess.run([str(python),str(ROOT/'scripts/gowin_probe.py'),'--worker','pnr','--out',str(out)],
                cwd=python.parent,env=dict(os.environ,PYTHONHOME=sys.base_prefix),stdout=log,stderr=subprocess.STDOUT,timeout=args.timeout)
            result['returncode']=cp.returncode
        except subprocess.TimeoutExpired:
            result.update(status='timeout',pnr_wall_seconds=time.perf_counter()-start);save();raise
    result['pnr_wall_seconds']=time.perf_counter()-start
    result['netlist_sha256_after']=sha(netlist)
    errors=re.findall(r'^ERROR\s*\(.*$',(out/'pnr.log').read_text(errors='replace'),re.M)
    result['errors']=errors
    result['artifacts']={p.name:dict(bytes=p.stat().st_size,sha256=sha(p)) for p in out.iterdir() if p.suffix in ('.vo','.tr','.fs')}
    expected=['mapped.vo','mapped.tr']+(['mapped.fs'] if args.bitstream and not args.rao else [])
    ok=cp.returncode==0 and not errors and all((out/name).is_file() for name in expected)
    if args.bitstream and args.rao:ok=ok and bool(list(out.glob('ao_*.fs')))
    ok=ok and result['netlist_sha256_before']==result['netlist_sha256_after']
    result['status']='complete' if ok else 'failed';save();print(json.dumps(result,indent=2))
    if not ok:raise RuntimeError('Full-project P&R did not complete; inspect native log')


if __name__=='__main__':main()
