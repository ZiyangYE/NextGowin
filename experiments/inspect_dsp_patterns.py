"""Read-only inventory of packable arithmetic around our inferred DSPs."""
import argparse
from collections import Counter,defaultdict
import json
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from yosys_runtime import run,quote

ap=argparse.ArgumentParser();ap.add_argument('source',type=Path);ap.add_argument('--out',type=Path,required=True);args=ap.parse_args()
out=args.out.resolve();out.mkdir(parents=True)
run(['read_rtlil '+quote(args.source.resolve()),'blackbox =A:whitebox','write_json '+quote(out/'coarse.json')],out,'read')
design=json.loads((out/'coarse.json').read_text());rows=[];summary=Counter()
for mn,m in design['modules'].items():
    loads=defaultdict(list)
    for n,c in m.get('cells',{}).items():
        for p,bs in c['connections'].items():
            if c['port_directions'].get(p) in ('input','inout'):
                for b in bs:loads[b].append((n,p))
    for name,c in m.get('cells',{}).items():
        if c['type'] not in ('MULTALU27X18','MULT12X12','MULT27X36'):continue
        consumers={n for b in c['connections']['DOUT'] for n,p in loads[b]}
        kinds=Counter(m['cells'][n]['type'] for n in consumers);summary.update(kinds)
        rows.append(dict(module=mn,instance=name,kind=c['type'],consumer_types=dict(kinds),
                         consumers=[dict(name=n,type=m['cells'][n]['type'],parameters=m['cells'][n]['parameters']) for n in sorted(consumers)]))
(out/'inventory.json').write_text(json.dumps(dict(summary=dict(summary),dsps=rows),indent=2)+'\n')
print('DSPs',len(rows),'consumer types',dict(summary))
print('Direct arithmetic:',[(r['module'],r['instance'],r['consumer_types']) for r in rows if '$add' in r['consumer_types'] or '$sub' in r['consumer_types']][:20])
