"""Report state/control/data load changes; not an electrical timing model."""
import argparse,json
from collections import Counter,defaultdict
from pathlib import Path
from ownpr_image_storage import load_json,sha
from workspace_budget import reserve
ROOT=Path(__file__).resolve().parents[1]
CONTROLS={'CLK','CLKA','CLKB','CE','CEA','CEB','RESET','SET','OCE','OCEA','OCEB'}

def inventory(design):
    controls=Counter();loads=Counter();drivers=defaultdict(list);types=Counter()
    for name,c in design['modules']['top']['cells'].items():
        types[c['type']]+=1
        for pin,bits in c['connections'].items():
            for bit in bits:
                if not isinstance(bit,int):continue
                if c['port_directions'][pin]=='input':
                    loads[bit]+=1
                    if pin in CONTROLS:controls[bit]+=1
                elif c['type'] not in ('GND','VCC'):drivers[bit].append(name+'/'+pin)
    return controls,loads,drivers,types

def compare(before,after):
    a,b=inventory(before),inventory(after);rows=[]
    for bit in a[2].keys()|b[2].keys():
        # Bit identity is only trusted when its original driver is unchanged.
        if bit not in a[2] or a[2][bit]!=b[2].get(bit):continue
        if a[0][bit]!=b[0][bit] or a[1][bit]!=b[1][bit]:
            rows.append(dict(bit=bit,drivers=a[2][bit],control_loads_before=a[0][bit],control_loads_after=b[0][bit],total_loads_before=a[1][bit],total_loads_after=b[1][bit]))
    return dict(status='structural_load_diagnostic',electrical_timing_verified=False,cell_delta={k:b[3][k]-a[3][k] for k in a[3].keys()|b[3].keys() if b[3][k]!=a[3][k]},unchanged_driver_load_changes=sorted(rows,key=lambda r:r['total_loads_after']-r['total_loads_before'],reverse=True),changed_or_new_driver_bits=sorted(k for k in b[2] if a[2].get(k)!=b[2][k]))

if __name__=='__main__':
    p=argparse.ArgumentParser()
    for name in ('before','after','out'):p.add_argument('--'+name,type=Path,required=True)
    a=p.parse_args();reserve(ROOT,5_000_000);result=compare(load_json(a.before),load_json(a.after));result.update(before_sha256=sha(a.before),after_sha256=sha(a.after))
    with a.out.open('x') as stream:json.dump(result,stream,indent=2)
    print(json.dumps(result['cell_delta']))
