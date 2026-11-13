"""Cold flow with a retained method snapshot and one shared wall deadline."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import subprocess
import sys
import time
from run_abc_reproduction import ROOT, sha


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--config',type=Path,required=True)
    ap.add_argument('--out',type=Path,required=True)
    ap.add_argument('--total-timeout',type=int,default=3600)
    args=ap.parse_args()
    if not 0<args.total_timeout<=3600:ap.error('Total wall budget must be within one hour')
    start=time.monotonic();out=args.out.resolve();out.mkdir(parents=True,exist_ok=False)
    inputs=sorted((ROOT/'scripts').glob('*.py'))+sorted((ROOT/'configs').glob('*.json'))
    inputs+=[ROOT/'results/timing/gowin138_model.json',ROOT/'results/timing/gowin138_model_ff_checked.json']
    hashes={p.relative_to(ROOT).as_posix():sha(p) for p in inputs}
    for name,digest in hashes.items():
        target=out/'method_source'/name;target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(ROOT/name,target)
        if sha(target)!=digest:raise ValueError('Method changed during snapshot')
    config=out/'input_config.json';shutil.copyfile(args.config,config)
    record=dict(status='running',started_utc=datetime.now(timezone.utc).isoformat(),
                source_config_sha256=sha(config),method_sha256=hashes,total_timeout_seconds=args.total_timeout,
                scope='Cold own synthesis plus native implementation. Snapshot is evidence, not a superiority claim.')
    def save():(out/'manifest.json').write_text(json.dumps(record,indent=2)+'\n')
    save()
    try:
        remaining=args.total_timeout-(time.monotonic()-start)
        with (out/'flow.log').open('w') as log:
            cp=subprocess.run([sys.executable,str(ROOT/'scripts/nextgowin.py'),'--config',str(config),
                               '--out',str(out/'flow'),'--total-timeout',str(max(1,int(remaining-2)))],
                              stdout=log,stderr=subprocess.STDOUT,timeout=remaining)
        record['returncode']=cp.returncode
        if any(sha(ROOT/name)!=digest for name,digest in hashes.items()):
            raise ValueError('Method changed during cold run; result cannot be pooled')
        child=json.loads((out/'flow/manifest.json').read_text())
        record.update(status=child['status'],timing_closed=child.get('timing_closed'),
                      child_manifest_sha256=sha(out/'flow/manifest.json'))
        if cp.returncode:raise RuntimeError('Cold flow failed; inspect retained flow/manifest.json')
    except Exception as error:
        record.update(status='failed',error=repr(error))
    record.update(wall_seconds=time.monotonic()-start,within_hour=time.monotonic()-start<=args.total_timeout)
    if not record['within_hour']:record['status']='failed'
    save();print(json.dumps({k:v for k,v in record.items() if k!='method_sha256'},indent=2))
    if record['status']!='complete':raise SystemExit(1)


if __name__=='__main__':main()
