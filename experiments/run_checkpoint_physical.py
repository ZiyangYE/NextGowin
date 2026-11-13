"""Implement a proved checkpoint, counting preparation/wait against one hour."""
import argparse
from datetime import datetime,timezone
import json
from pathlib import Path
import subprocess
import sys
import time
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from flow_budget import Budget
from project_timing import audit
from compare_project_timing import compare
from run_abc_reproduction import sha

ap=argparse.ArgumentParser();ap.add_argument('--checkpoint',type=Path,required=True)
ap.add_argument('--config',type=Path,required=True);ap.add_argument('--baseline',type=Path,required=True)
args=ap.parse_args();out=args.checkpoint.resolve();config=json.loads(args.config.read_text())
created=out.stat().st_ctime;budget=Budget(3600,max(0,time.time()-created))
prep=json.loads((out/'manifest.json').read_text());vg=out/'05_netlist/mapped.vg'
if prep['status']!='proved_emitted_checkpoint' or sha(vg)!=prep['output_netlist_sha256']:
    raise ValueError('Checkpoint has no matching proved emitted netlist')
if sha(ROOT/'experiments/register_fanout_replication.py')!=prep['implementation_sha256']:
    raise ValueError('Register-copy method changed after preparation')
cluster=prep.get('cluster_replication')
if cluster and sha(ROOT/'experiments/dedicated_cluster_replication.py')!=cluster['implementation_sha256']:
    raise ValueError('Cluster-copy method changed after preparation')
def path(key):
    p=Path(config[key]);return p.resolve() if p.is_absolute() else ROOT/p
record=dict(status='running',scope='Own proved checkpoint, not cold RTL synthesis. Preparation and waiting count against one hour.',
    started_utc=datetime.fromtimestamp(created,timezone.utc).isoformat(),prior_seconds=budget.used(),
    config_sha256=sha(args.config),preparation_manifest_sha256=sha(out/'manifest.json'),
    netlist_sha256=sha(vg),runner_sha256=sha(Path(__file__)))
def save():(out/'implementation_result.json').write_text(json.dumps(record,indent=2)+'\n')
save()
try:
    timeout=int(budget.timeout(3600,reserve=10))
    if timeout<1:raise TimeoutError('No checkpoint budget remains')
    command=[sys.executable,str(ROOT/'scripts/run_gowin_netlist.py'),str(vg),'--out',str(out/'06_pnr'),
             '--sdc',str(path('sdc')),'--bitstream','--timeout',str(timeout)]
    if config.get('cst'):command+=['--cst',str(path('cst'))]
    if config.get('no_iob_registers'):command+=['--no-iob-registers']
    with (out/'physical_runner.log').open('w') as log:
        subprocess.run(command,stdout=log,stderr=subprocess.STDOUT,check=True,timeout=budget.remaining())
    profile=json.loads(path('physical_profile').read_text());candidate=audit(out/'06_pnr',profile)
    (out/'06_pnr/physical_summary.json').write_text(json.dumps(candidate,indent=2)+'\n')
    paired=compare(audit(args.baseline.resolve(),profile),candidate)
    (out/'paired_comparison.json').write_text(json.dumps(paired,indent=2)+'\n')
    budget.remaining()
    record.update(status='complete',timing_closed=candidate['timing_closed'],
                  frequency_reports=candidate['frequency_reports'],violated_endpoints=candidate['violated_endpoints'])
except Exception as error:record.update(status='failed',error=repr(error))
record.update(wall_seconds_including_preparation_and_wait=budget.used(),within_hour=budget.used()<=3600)
if not record['within_hour']:record['status']='failed'
save();print(json.dumps(record,indent=2))
if record['status']!='complete':raise SystemExit(1)
