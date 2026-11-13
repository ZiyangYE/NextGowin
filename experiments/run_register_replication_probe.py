"""Retain a checked FF-replication proposal; this does not run P&R."""
import argparse
import json
from pathlib import Path
import time
from register_fanout_replication import replicate,validate_graph,prove_sdk
from run_abc_reproduction import sha

ap=argparse.ArgumentParser()
ap.add_argument('--source',type=Path,required=True)
ap.add_argument('--out',type=Path,required=True)
args=ap.parse_args();start=time.monotonic()
source=args.source.resolve();out=args.out.resolve();out.mkdir(parents=True,exist_ok=False)
before=json.loads(source.read_text());after,record=replicate(before)
record['graph_certificate']=validate_graph(before,after,record)
record['transition_proof']=prove_sdk(out/'proof')
record.update(status='proved_proposal_only',source_sha256=sha(source),wall_seconds=time.monotonic()-start,
              physical_result_available=False)
(out/'candidate.json').write_text(json.dumps(after)+'\n')
record['candidate_sha256']=sha(out/'candidate.json')
(out/'manifest.json').write_text(json.dumps(record,indent=2)+'\n')
print('Cloned FFs:',record['clones'],'wall:',record['wall_seconds'])
