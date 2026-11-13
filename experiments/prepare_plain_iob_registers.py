"""Check whether a full next-state D cover enables all output IOB registers."""
import json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from register_alternatives import add
from map_resource_json import map_design
from nextgowin import materialize
from registered_benchmark import prove
from gowin_blif import read_blif

out=ROOT/'results/int2float_plain_capture_002';out.mkdir(exist_ok=False);r=out/'03_resources';r.mkdir()
source=json.loads((ROOT/'results/int2float_hclk_002/lut4/netlist/source.json').read_text());d,record=add(source)
(r/'resources.json').write_text(json.dumps(d)+'\n');(r/'register_choices.json').write_text(json.dumps(record,indent=2)+'\n')
for m in d['modules'].values():
    for c in m.get('cells',{}).values():
        if c['type']=='$_MUX_':
            p=c['connections'];c.update(type='LUT3',parameters={'INIT':'11001010'},connections=dict(I0=p['A'],I1=p['B'],I2=p['S'],F=p['Y']),port_directions=dict(I0='input',I1='input',I2='input',F='output'))
(out/'04_mapping').mkdir();(out/'04_mapping/mapped.json').write_text(json.dumps(d)+'\n')
materialize(out/'04_mapping/mapped.json',out/'05_netlist','top',optimize_resources=False,register_alternatives=True,register_control_penalty_ps=2000)
proof=out/'functional_proof';proof.mkdir();reference=ROOT/'third_party/epfl/random_control/int2float.blif';(proof/'reference.blif').write_bytes(reference.read_bytes())
result=prove(json.loads((out/'05_netlist/final.json').read_text()),read_blif(reference,max_inputs=11),proof)
print(json.dumps(dict(choices=len(record['choices']),proof=result),indent=2))
