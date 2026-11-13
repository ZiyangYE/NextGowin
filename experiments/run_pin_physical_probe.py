"""Fixed-source, same-constraint development probes of physical LUT pin use."""
import argparse
import json
from pathlib import Path
import sys
import time
from pin_aware_lut_materialization import optimize,ROOT
from native_boundary_cec import prove
from nextgowin import materialize
from project_timing import audit
from run_fanout_development_suite import physical
from run_abc_reproduction import sha

ap=argparse.ArgumentParser();ap.add_argument('--out',type=Path,required=True)
ap.add_argument('--designs',nargs='+',choices=['ctrl','priority','multiplier'],default=['ctrl','priority']);args=ap.parse_args()
out=args.out.resolve();out.mkdir(parents=True,exist_ok=False)
result=dict(status='running',designs_predeclared=args.designs,runs={},scope='Observed development designs. Own checkpoint, not cold RTL timing or a general superiority claim.')
def save():(out/'manifest.json').write_text(json.dumps(result,indent=2)+'\n')
save()
for name in args.designs:
    start=time.monotonic();p=out/name;p.mkdir();row=dict(status='running');result['runs'][name]=row;save()
    try:
        source=ROOT/'results/fanout_development_suite_001'/name
        before=json.loads((source/'own/05_netlist/final.json').read_text());after,record=optimize(before)
        record['cec']=prove(before,after,p/'proposal_cec');(p/'pin_mapping.json').write_text(json.dumps(record,indent=2)+'\n')
        mapping=p/'04_mapping';mapping.mkdir();path=mapping/'mapped.json';path.write_text(json.dumps(after)+'\n')
        materialize(path,p/'05_netlist','top',source/'own/02_bridge/vendor_cells.v',optimize_resources=False)
        row['actual_emitted_cec']=prove(before,json.loads((p/'05_netlist/final.json').read_text()),p/'actual_cec')
        base=audit(source/'own_pnr',dict(part='GW5AST-LV138FPG676AC1/I0',clock_periods_ns={'clk':10.0}))
        row['source_fmax_mhz']=base['frequency_reports']['clk']['reported_fmax_mhz']
        row['vendor_fmax_mhz']=json.loads((source.parent/'manifest.json').read_text())['runs'][name]['vendor']['fmax_mhz']
        row['candidate']=physical(p/'05_netlist/mapped.vg',source/'clock.sdc',p/'pnr',300)
        for field in ('part','dll_sha256','delay_models','clock_periods_ns','implementation_options'):
            if base[field]!=row['candidate']['timing'][field]:raise ValueError('Implementation comparison differs: '+field)
        if sha(source/'clock.sdc')!=sha(p/'pnr/project.sdc'):raise ValueError('Constraint content differs')
        row.update(status='complete',changed=record['changed'],source_json_sha256=sha(source/'own/05_netlist/final.json'),ratio_to_source=row['candidate']['fmax_mhz']/row['source_fmax_mhz'])
        print(name,'changed',row['changed'],'source',row['source_fmax_mhz'],'pin',row['candidate']['fmax_mhz'],'vendor',row['vendor_fmax_mhz'],flush=True)
    except Exception as error:row.update(status='failed',error=repr(error));print(name,'FAILED',repr(error),flush=True)
    row['wall_seconds']=time.monotonic()-start;save()
result['status']='complete' if all(r['status']=='complete' for r in result['runs'].values()) else 'failed';save()
