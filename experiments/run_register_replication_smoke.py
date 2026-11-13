"""Native-netlist FF-copy smoke test with actual emitted readback and P&R."""
import argparse
import copy
import json
from pathlib import Path
import subprocess
import sys
from register_fanout_replication import replicate,validate_graph,prove_sdk,ROOT
from nextgowin import materialize
from native_boundary_cec import prove
from project_timing import parse

ap=argparse.ArgumentParser();ap.add_argument('--out',type=Path,required=True);args=ap.parse_args()
out=args.out.resolve();out.mkdir(parents=True,exist_ok=False)
cells={};ports={'clk':dict(direction='input',bits=[2])}
for i in range(60):
    ports['a'+str(i)]=dict(direction='input',bits=[4+i])
    if i:ports['q'+str(i)]=dict(direction='output',bits=[100+i])
    cells['ff'+str(i)]=dict(type='DFFRE',parameters={'INIT':'0'},attributes={},
        port_directions={'D':'input','CLK':'input','CE':'input','RESET':'input','Q':'output'},
        connections={'D':[1000+i if i else 4],'CLK':[2],'CE':['1'],'RESET':['0'],'Q':[100+i]})
    if i:
        cells['logic'+str(i)]=dict(type='LUT2',parameters={'INIT':'0110'},attributes={},
            port_directions={'I0':'input','I1':'input','F':'output'},
            connections={'I0':[100],'I1':[4+i],'F':[1000+i]})
before={'modules':{'top':dict(attributes={'top':'1'},ports=ports,netnames={},cells=cells)}}
after,record=replicate(before,limit=24,area_allowance=.02)
if record['clones']!=1:raise ValueError('Expected one copy in smoke fixture')
record['graph_proof']=validate_graph(before,after,record);record['state_proof']=prove_sdk(out/'state_proof')
for name,design in [('before',before),('after',after)]:
    folder=out/name;folder.mkdir();mapping=folder/'04_mapping';mapping.mkdir()
    path=mapping/'mapped.json';path.write_text(json.dumps(design)+'\n')
    materialize(path,folder/'05_netlist','top',optimize_resources=False)
actuals=[json.loads((out/name/'05_netlist/final.json').read_text()) for name in ('before','after')]
collapsed=copy.deepcopy(actuals[1]);m=collapsed['modules']['top'];aliases={}
for row in record['copies']:
    aliases[m['cells'][row['clone']]['connections']['Q'][0]]=m['cells'][row['source']]['connections']['Q'][0]
    source=m['cells'][row['source']];clone=m['cells'][row['clone']]
    if any(source[k]!=clone[k] for k in ('type','parameters','port_directions')):raise ValueError('Emitted copy metadata changed')
    if any(source['connections'][p]!=bs for p,bs in clone['connections'].items() if p!='Q'):
        raise ValueError('Emitted copy controls differ')
    del m['cells'][row['clone']]
for c in m['cells'].values():
    for pin,bs in c['connections'].items():c['connections'][pin]=[aliases.get(b,b) for b in bs]
record['emitted_boundary_cec']=prove(actuals[0],collapsed,out/'emitted_cec')
sdc=out/'clock.sdc';sdc.write_text('create_clock -name clk -period 10 [get_ports {clk}]\n')
with (out/'pnr_runner.log').open('w') as log:
    subprocess.run([sys.executable,str(ROOT/'scripts/run_gowin_netlist.py'),str(out/'after/05_netlist/mapped.vg'),
        '--sdc',str(sdc),'--out',str(out/'pnr'),'--no-iob-registers','--bitstream','--timeout','180'],
        stdout=log,stderr=subprocess.STDOUT,check=True,timeout=190)
record['physical']=parse((out/'pnr/mapped.tr').read_text(),sdc.read_text(),
    dict(part='GW5AST-LV138FPG676AC1/I0',clock_periods_ns={'clk':10.0}))
record['status']='smoke_complete_not_qor_comparison'
(out/'manifest.json').write_text(json.dumps(record,indent=2)+'\n')
print('Clones',record['clones'],'timing closed',record['physical']['timing_closed'])
