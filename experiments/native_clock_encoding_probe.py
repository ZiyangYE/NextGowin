"""Device-only clock-tree encoding differential; never a hardware image."""
import argparse,json,os,shutil,subprocess,sys,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from gowin_probe import prepare
from run_abc_reproduction import sha
from capture_runtime_tables import EXPECTED


def worker(out,debug=False,prefix_reference=False):
    from native_device_session import NativeDeviceSession
    import ctypes as C
    native=NativeDeviceSession(out)
    tree_file=out/'clock_tree.json' if (out/'clock_tree.json').exists() else ROOT/'results/ownpr_clock_tree_001/clock_tree.json';tree=json.loads(tree_file.read_text())
    parents={tuple(r['node']):tuple(r['parent']) if r['parent'] else None for r in tree['parents']}
    if prefix_reference:
        reference=json.loads((ROOT/'results/native_global_clock_001/native_wire_delays.json').read_text())
        path=next(p['path_sink_to_source'] for p in reference['paths'] if p['native_pin_type']==670)
        prefix=path[path.index([-2,-1,132]):]
        if tuple(prefix[-1])!=tuple(tree['source']):raise ValueError('Reference uses a different clock pad')
        for node,parent in zip(prefix,prefix[1:]):parents[tuple(node)]=tuple(parent)
    applied=out/'applied_parents.json';applied.write_text(json.dumps([dict(node=n,parent=p) for n,p in sorted(parents.items())],indent=2))
    pointers=native.resolve_routed_nodes(parents)
    encode=native.fn('?generateFusemap@@YAHPEAVglobalMem@@@Z',C.c_int,[C.c_void_p]);fs=out/'probe.fs'
    if encode(native.mem):raise ValueError('Initial device encoding failed')
    shutil.copyfile(fs,out/'encoding_first.fs')
    if encode(native.mem):raise ValueError('Baseline device encoding failed')
    shutil.copyfile(fs,out/'encoding_baseline.fs')
    with native.resolved_parent_tree(parents,pointers):
        if debug:
            target=pointers[(-2,-1,30)]
            print('CLOCK_PARENT_WATCH',hex(target+0x18),flush=True)
            C.windll.kernel32.DebugBreak()
        if encode(native.mem):raise ValueError('Clock-tree encoding failed')
        shutil.copyfile(fs,out/'encoding_clock.fs')
    if encode(native.mem):raise ValueError('Restored device encoding failed')
    shutil.copyfile(fs,out/'encoding_restored.fs')
    from ownpr_device import Device
    from ownpr_lut_configuration import NativeFrameProjection
    import numpy as np
    projection=NativeFrameProjection(Device())
    baseline=projection.read(out/'encoding_baseline.fs');clock=projection.read(out/'encoding_clock.fs');restored=projection.read(out/'encoding_restored.fs')
    a=np.frombuffer(baseline,dtype=np.uint8);b=np.frombuffer(clock,dtype=np.uint8);c=np.frombuffer(restored,dtype=np.uint8)
    changed=np.flatnonzero(a!=b);unstable=np.flatnonzero(a!=c)
    result=dict(status='reversible_clock_data_difference' if not len(unstable) and len(changed) else 'inconclusive',
        changed_data_bits=len(changed),restore_difference_bits=len(unstable),
        changes=[dict(index=int(i),before=int(a[i])-48,after=int(b[i])-48) for i in changed],
        clock_tree_sha256=sha(tree_file),applied_parents_sha256=sha(applied),prefix_reference=prefix_reference,
        baseline_sha256=sha(out/'encoding_baseline.fs'),
        clock_sha256=sha(out/'encoding_clock.fs'),restored_sha256=sha(out/'encoding_restored.fs'),
        vendor_pnr_executed=False,configuration_semantics_verified=False,full_bitstream_verified=False,
        scope='Differential serialization of a manually installed physical clock tree in an otherwise unplaced device. Bit semantics and functional image validity are not inferred.')
    (out/'encoding_audit.json').write_text(json.dumps(result,indent=2))
    print({k:v for k,v in result.items() if k!='changes'},flush=True)


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--out',type=Path,required=True);ap.add_argument('--worker',action='store_true');ap.add_argument('--debug',action='store_true');ap.add_argument('--prefix-reference',action='store_true');ap.add_argument('--compact',action='store_true');ap.add_argument('--tree',type=Path,default=ROOT/'results/ownpr_clock_tree_001/clock_tree.json');args=ap.parse_args();out=args.out.resolve()
    if args.debug and not args.worker:ap.error('--debug requires an attached worker debugger')
    if args.worker:return worker(out,args.debug,args.prefix_reference)
    from workspace_budget import reserve
    storage=reserve(ROOT,400_000_000)
    prepare(out);cmd=out/'cmd.do';cmd.write_text(cmd.read_text()+'-bit\n');shutil.copyfile(__file__,out/'extractor_source.py.txt')
    shutil.copyfile(args.tree,out/'clock_tree.json')
    python=ROOT/'binary_audit/flow/runtime/bin/python.exe';start=time.monotonic();rc=None;expired=False
    with (out/'worker.log').open('w') as log:
        try:rc=subprocess.run([str(python),str(Path(__file__).resolve()),'--worker','--out',str(out)]+(['--prefix-reference'] if args.prefix_reference else []),cwd=python.parent,
            env=dict(os.environ,PYTHONHOME=sys.base_prefix),stdout=log,stderr=subprocess.STDOUT,timeout=120).returncode
        except subprocess.TimeoutExpired:expired=True
    result=dict(status='captured' if rc==0 and (out/'encoding_audit.json').exists() else 'failed',returncode=rc,timeout=expired,
        wall_seconds=time.monotonic()-start,dll_sha256=EXPECTED,open_chip_database_used=False,vendor_pnr_executed=False,device_programmed=False)
    result['storage_reservation']=storage
    if result['status']=='captured' and args.compact:
        from ownpr_clock_images import compact
        reserve(ROOT,200_000_000);result['image_storage']=compact(out);result['wall_seconds']=time.monotonic()-start
    (out/'manifest.json').write_text(json.dumps(result,indent=2));print(json.dumps(result,indent=2))
    if result['status']=='failed':raise SystemExit(1)


if __name__=='__main__':main()
