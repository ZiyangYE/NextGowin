"""Find only this workspace's isolated native probe workers, not arbitrary Python."""
import argparse
import json
from pathlib import Path
import time
import psutil

ROOT=Path(__file__).resolve().parents[1]
ap=argparse.ArgumentParser();ap.add_argument('--terminate-failed',action='store_true');args=ap.parse_args()
rows=[]
for process in psutil.process_iter(['pid','name']):
    if process.info['name'].lower()!='python.exe':continue
    try:
        command=process.cmdline()
        if len(command)<2 or Path(command[1]).resolve()!=ROOT/'scripts/gowin_probe.py':continue
        if '--worker' not in command or '--out' not in command:continue
        out=Path(command[command.index('--out')+1]).resolve()
        if not out.is_relative_to(ROOT):continue
        manifest=out/'manifest.json'
        state=json.loads(manifest.read_text()) if manifest.exists() else {}
        seconds=time.time()-process.create_time()
        failed=state.get('status') in ('failed','timeout')
        row=dict(pid=process.pid,output=str(out.relative_to(ROOT)),status=state.get('status'),
                 elapsed_seconds=seconds,parent_pid=process.ppid(),failed_manifest=failed)
        if args.terminate_failed and failed and seconds>state.get('timeout',3600):
            process.kill();row['terminated_failed_worker']=True
        rows.append(row)
    except (psutil.Error,OSError,ValueError):continue
print(json.dumps(rows,indent=2))
