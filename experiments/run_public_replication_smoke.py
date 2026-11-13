"""Exercise both public replication flags and emitted CEC, then a real bitstream."""
import argparse
import copy
import json
from pathlib import Path
import subprocess
import sys
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'));sys.path.insert(0,str(ROOT/'tests'))
from test_dedicated_cluster_replication import fixture
from nextgowin import materialize
from project_timing import parse

ap=argparse.ArgumentParser();ap.add_argument('--out',type=Path,required=True);args=ap.parse_args()
out=args.out.resolve();out.mkdir(parents=True,exist_ok=False)
design=fixture();m=design['modules']['top'];cells=m['cells']
for i in range(100,200):
    c=copy.deepcopy(cells['ng_acd_leaf_0']);c['connections']['F']=[1000+i];cells['ng_acd_leaf_'+str(i)]=c
for i in range(2,200):m['ports']['leaf'+str(i)]=dict(direction='output',bits=[1000+i])
for i in range(70):
    cells['ff'+str(i)]['connections']['CE']=[30000]
    m['ports']['q'+str(i)]=dict(direction='output',bits=[20000+i])
for i in range(171):
    c=copy.deepcopy(cells['ff0']);c['connections'].update(D=[2000 if i==0 else 21000+i-1],Q=[21000+i])
    cells['pipeline'+str(i)]=c
cells['ff0']['connections']['D']=[21170]
control=copy.deepcopy(cells['ff0']);control['connections'].update(D=[3],Q=[30000],CE=['1']);cells['control']=control
mapping=out/'04_mapping';mapping.mkdir();p=mapping/'mapped.json';p.write_text(json.dumps(design)+'\n')
materialize(p,out/'05_netlist','top',optimize_resources=False,register_replication=True,dedicated_cluster_replication=True)
record=json.loads((out/'05_netlist/native_replication.json').read_text())
if record['registers']['clones']!=1 or record['clusters']['clusters']!=1:
    raise ValueError('Both public transformations must be exercised')
sdc=out/'clock.sdc';sdc.write_text('create_clock -name clk -period 10 [get_ports {clk}]\n')
with (out/'pnr_runner.log').open('w') as log:
    subprocess.run([sys.executable,str(ROOT/'scripts/run_gowin_netlist.py'),str(out/'05_netlist/mapped.vg'),
        '--sdc',str(sdc),'--out',str(out/'pnr'),'--bitstream','--no-iob-registers','--timeout','180'],
        stdout=log,stderr=subprocess.STDOUT,check=True,timeout=190)
timing=parse((out/'pnr/mapped.tr').read_text(),sdc.read_text(),dict(part='GW5AST-LV138FPG676AC1/I0',clock_periods_ns={'clk':10.0}))
record.update(status='public_smoke_complete',timing_closed=timing['timing_closed'],violated_endpoints=timing['violated_endpoints'])
(out/'manifest.json').write_text(json.dumps(record,indent=2)+'\n')
print('Copies',record['registers']['clones'],'clusters',record['clusters']['clusters'],'timing',record['violated_endpoints'])
