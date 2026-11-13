"""Capture a native STA fault on an own-P&R handoff, without native P&R."""
import argparse,json,os,shutil,subprocess,sys,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
from gowin_probe import prepare


def main():
    p=argparse.ArgumentParser();p.add_argument('--source',type=Path,required=True);p.add_argument('--placement',type=Path,required=True);p.add_argument('--out',type=Path,required=True);a=p.parse_args()
    source=a.source.resolve();out=a.out.resolve();prepare(out)
    for name in ('probe.vg','name_map.json','project.cst','probe.sdc','clock_tree.json','expanded_routes.json','partial_configuration.json','io_reference.json'):
        shutil.copyfile(source/name,out/name)
    cmd=out/'cmd.do';cmd.write_text(cmd.read_text()+f'-cst {(out/"project.cst").as_posix()}\n-bit\n')
    commands=out/'fault.cdb'
    commands.write_text('sxe av\ng\n.echo OWNPR_STA_FAULT\nr\n.exr -1\nkn 18\nu @rip-20 @rip+20\n.printf "FAULT_RVA=%p\\n", @rip-gowin\nq\n')
    python=ROOT/'binary_audit/flow/runtime/bin/python.exe';start=time.monotonic()
    with (out/'debugger.stdout').open('w') as log:
        result=subprocess.run(['C:/Program Files (x86)/Windows Kits/10/Debuggers/x64/cdb.exe','-G','-logo',str(out/'fault.log'),'-cf',str(commands),str(python),
            str(ROOT/'experiments/native_import_ownpr.py'),'--worker','--export','--sta','--out',str(out),'--placement',str(a.placement.resolve())],
            cwd=python.parent,env=dict(os.environ,PYTHONHOME=sys.base_prefix),stdout=log,stderr=subprocess.STDOUT,timeout=60,creationflags=subprocess.CREATE_NO_WINDOW)
    report=dict(returncode=result.returncode,wall_seconds=time.monotonic()-start,source=str(source),vendor_placement_executed=False,vendor_routing_executed=False,
        scope='Native first-chance exception diagnosis, not a completed or valid export')
    (out/'manifest.json').write_text(json.dumps(report,indent=2));print(json.dumps(report))


if __name__=='__main__':main()
