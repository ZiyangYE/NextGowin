"""Continue a verified native baseline into a serial optimized-project P&R run."""
import argparse
import ctypes
from ctypes import wintypes
import json
from pathlib import Path
import subprocess
import sys
import time
from project_timing import audit
from run_abc_reproduction import ROOT,sha


def wait_worker(pid,update):
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    kernel.OpenProcess.argtypes=[wintypes.DWORD,wintypes.BOOL,wintypes.DWORD]
    kernel.OpenProcess.restype=wintypes.HANDLE
    kernel.QueryFullProcessImageNameW.argtypes=[wintypes.HANDLE,wintypes.DWORD,wintypes.LPWSTR,ctypes.POINTER(wintypes.DWORD)]
    kernel.WaitForSingleObject.argtypes=[wintypes.HANDLE,wintypes.DWORD]
    kernel.WaitForSingleObject.restype=wintypes.DWORD
    kernel.CloseHandle.argtypes=[wintypes.HANDLE]
    handle=kernel.OpenProcess(0x1000|0x100000,False,pid)
    if not handle:raise ctypes.WinError(ctypes.get_last_error())
    try:
        path=ctypes.create_unicode_buffer(32768);length=wintypes.DWORD(len(path))
        if not kernel.QueryFullProcessImageNameW(handle,0,path,ctypes.byref(length)):
            raise ctypes.WinError(ctypes.get_last_error())
        expected=ROOT/'binary_audit/flow/runtime/bin/python.exe'
        if Path(path.value).resolve()!=expected.resolve():raise ValueError('Wait PID is not the known isolated P&R worker')
        update(dict(pid=pid,executable=path.value,live_handle_confirmed=True))
        while True:
            code=kernel.WaitForSingleObject(handle,1000)
            if code==0:break
            if code!=258:raise ctypes.WinError(ctypes.get_last_error())
    finally:
        kernel.CloseHandle(handle)


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--baseline',type=Path,required=True);ap.add_argument('--candidate',type=Path,required=True)
    ap.add_argument('--rewrite-audit',type=Path,required=True);ap.add_argument('--profile',type=Path,required=True)
    ap.add_argument('--wait-pid',type=int);ap.add_argument('--timeout',type=int,default=3600)
    ap.add_argument('--out',type=Path,required=True);args=ap.parse_args()
    out=args.out.resolve();out.mkdir(parents=True,exist_ok=False)
    result=dict(status='starting',baseline=str(args.baseline.resolve()),candidate_sha256=sha(args.candidate))
    def save():(out/'manifest.json').write_text(json.dumps(result,indent=2)+'\n')
    save()
    try:
        proof=json.loads(args.rewrite_audit.read_text())
        if proof.get('collapse_copies_restores_original_lut_graph'):
            original_hash=proof['input_sha256'];candidate_hash=proof['output_sha256']
            verified=proof.get('non_lut_statements_preserved') and proof['cec']['equivalent']
            result['proof_type']='same_cut_replication_with_global_boundary_cec'
        else:
            original_hash=proof['original_sha256'];candidate_hash=proof['candidate_sha256']
            verified=all(r['cec']['equivalent'] for r in proof['checks'])
            result['proof_type']='composed_module_boundary_cec'
        if proof['status']!='complete' or candidate_hash!=sha(args.candidate) or not verified:
            raise ValueError('Final composed rewrite proof missing or candidate changed')
        if args.wait_pid:
            def update(worker):result.update(status='waiting_baseline_worker',waited_worker=worker);save()
            wait_worker(args.wait_pid,update)
        # The native worker may finish just before its parent writes final hashes.
        for _ in range(30):
            native=json.loads((args.baseline/'manifest.json').read_text())
            if 'returncode' in native:break
            time.sleep(.2)
        if native['input_sha256']!=original_hash:
            raise ValueError('Baseline/rewrite original netlist mismatch')
        profile=json.loads(args.profile.read_text())
        baseline=audit(args.baseline,profile)
        (out/'baseline_summary.json').write_text(json.dumps(baseline,indent=2)+'\n')
        result.update(status='optimized_pnr_running',baseline_timing_closed=baseline['timing_closed']);save()
        print('Baseline independently audited; starting serial optimized P&R',flush=True)
        command=[sys.executable,str(ROOT/'scripts/run_gowin_netlist.py'),str(args.candidate.resolve()),
            '--sdc',str((args.baseline/'project.sdc').resolve()),'--cst',str((args.baseline/'project.cst').resolve()),
            '--timeout',str(args.timeout),'--out',str(out/'optimized')]
        with (out/'optimized_runner.log').open('w') as log:
            cp=subprocess.run(command,stdout=log,stderr=subprocess.STDOUT)
        if cp.returncode:raise RuntimeError('Optimized P&R runner failed; inspect optimized_runner.log')
        optimized=audit(out/'optimized',profile)
        (out/'optimized_summary.json').write_text(json.dumps(optimized,indent=2)+'\n')
        result.update(status='complete',optimized_timing_closed=optimized['timing_closed'],
            scope='One serial paired physical run; not a stable performance conclusion or board acceptance')
        save();print(json.dumps(result,indent=2))
    except Exception as error:
        result.update(status='failed',error=str(error));save();raise


if __name__=='__main__':main()
