"""Dry import of a complete own packed/placed hard design, without routing/STA."""
import argparse,json,os,shutil,subprocess,sys,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from ownpr_image_storage import sha


def main():
    p=argparse.ArgumentParser();p.add_argument('--placement',type=Path,required=True);p.add_argument('--out',type=Path,required=True);p.add_argument('--worker',action='store_true');a=p.parse_args();folder=a.placement.resolve();out=a.out.resolve()
    if a.worker:
        from native_import_ownpr import worker
        return worker(out,folder)
    from workspace_budget import reserve
    from gowin_probe import prepare
    from ownpr_native_netlist import emit
    from ownpr_hard_architecture import HardArchitecture
    from ownpr_device import DLL_SHA
    reserve(ROOT,100_000_000);prepare(out)
    packed=folder/'packed.json';placed=folder/'placement.json';inputs={str(f):sha(f) for f in (packed,placed)}
    text,mapping=emit(json.loads(packed.read_text()),hard=HardArchitecture())
    (out/'probe.vg').write_text(text);(out/'name_map.json').write_text(json.dumps(mapping,indent=2))
    sources=out/'sources';sources.mkdir()
    for f in [Path(__file__),ROOT/'experiments/native_import_ownpr.py',*[ROOT/'scripts'/n for n in ('ownpr_native_netlist.py','native_hard_binding.py','ownpr_hard_architecture.py','audit_ownpr_native_export.py','native_hard_import_audit.py')]]:shutil.copyfile(f,sources/f.name)
    python=ROOT/'binary_audit/flow/runtime/bin/python.exe';start=time.monotonic()
    with (out/'worker.log').open('w') as log:
        r=subprocess.run([str(python),str(Path(__file__).resolve()),'--worker','--placement',str(folder),'--out',str(out)],cwd=python.parent,env=dict(os.environ,PYTHONHOME=sys.base_prefix),stdout=log,stderr=subprocess.STDOUT,timeout=120)
    result=dict(status='placement_imported' if r.returncode==0 else 'failed',returncode=r.returncode,wall_seconds=time.monotonic()-start,
        source_sha256=inputs,helper_sha256={f.name:sha(f) for f in sources.iterdir()},dll_sha256=DLL_SHA,vendor_pnr_executed=False,
        routing_verified=False,timing_signoff=False,bitstream_generated=False,serialized_netlist_sha256=sha(out/'probe.vg'))
    if r.returncode==0:
        if any(sha(Path(f))!=h for f,h in inputs.items()):raise ValueError('Placement input changed')
        result['binding_report_sha256']=sha(out/'binding_report.json');result['connectivity_sha256']=sha(out/'native_connectivity.json')
        result['hard_readback_sha256']=sha(out/'hard_import_readback.json')
    (out/'manifest.json').write_text(json.dumps(result,indent=2));print(json.dumps(result,indent=2))
    if r.returncode:raise SystemExit(1)


if __name__=='__main__':main()
