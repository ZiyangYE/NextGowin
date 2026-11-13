"""Timing-only ABC9 adaptation, with unchanged upstream primitive functions.

Only LUT1-6 and synchronous DFFRE costs are adapted. These are analytical
synthesis costs from the pinned GW5A observations, not complete STA/signoff.
"""
import json
from pathlib import Path
import re
import sys
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from verific_bridge import units
from yosys_runtime import SUITE
from run_abc_reproduction import sha


def generate(out):
    out=Path(out).resolve();out.mkdir(parents=True)
    source=SUITE/'share/yosys/gowin/cells_sim.v';text=source.read_text();edits=[];changed=[]
    ff_model=ROOT/'results/timing/gowin138_model_ff_checked.json'
    data_setup=json.loads(ff_model.read_text())['ff_data_constraints']['setup']['envelope_ps']
    for name,(a,b) in units(text).items():
        if name not in ('LUT1','LUT2','LUT3','LUT4','__APICULA_LUT5','__APICULA_LUT6','DFFRE'):continue
        body=text[a:b];match=re.search(r'\bspecify\b.*?\bendspecify\b',body,re.S)
        if not match:raise ValueError('Expected upstream timing block missing')
        if name=='DFFRE':
            spec=f'specify\nif (CE) (posedge CLK => (Q : D)) = (344,344);\n$setup(D, posedge CLK &&& CE, {data_setup});\n$setup(CE, posedge CLK, 335);\n$setup(RESET, posedge CLK, 335);\nendspecify'
        else:
            k=int(name[-1]);delays=[521]*k if k<=4 else [721]*4+[250] if k==5 else [921]*4+[450,250]
            pins=['I'+str(i) for i in range(k)] if k<=4 else ['I0','I1','I2','I3','M0'] if k==5 else ['I0','I1','I2','I3','M0','M1']
            spec='specify\n'+'\n'.join(f'({p} => F) = ({v},{v});' for p,v in zip(pins,delays))+'\nendspecify'
        replacement=body[:match.start()]+spec+body[match.end():]
        strip=lambda t:re.sub(r'\bspecify\b.*?\bendspecify\b','',t,flags=re.S)
        if strip(replacement)!=strip(body):raise AssertionError('Primitive function changed')
        edits.append((a,b,replacement));changed.append(name)
    for a,b,replacement in reversed(edits):text=text[:a]+replacement+text[b:]
    output=out/'cells_sim.v';output.write_text(text)
    # Never replace SDK pad, DDR, PLL or DSP interface declarations with older
    # family models. Only these compatible logic/timing models are overlaid.
    compatible={'LUT1','LUT2','LUT3','LUT4','__APICULA_LUT5','__APICULA_LUT6','__APICULA_LUT7','__APICULA_LUT8','DFF','DFFE','DFFR','DFFS','DFFRE','DFFSE','DFFCE','DFFPE','DFFC','DFFP','ALU','MUX2','MUX2_LUT5','MUX2_LUT6','MUX2_LUT7','MUX2_LUT8'}
    # Include module attributes immediately preceding each declaration.
    spans=units(text);parts=[]
    for name,(a,b) in spans.items():
        if name not in compatible:continue
        start=a;prefix=text[max(0,a-150):a];attr=re.search(r'(\(\*[^*]*(?:\*(?!\))[^*]*)*\*\)\s*)$',prefix)
        if attr:start=a-len(prefix)+attr.start()
        parts.append(text[start:b])
    selected=out/'abc9_cells.v';selected.write_text('\n'.join(parts))
    record=dict(source_sha256=sha(source),output_sha256=sha(output),changed_timing_only=changed,
        selected_model_sha256=sha(selected),selected_modules=sorted(compatible & set(spans)),source_ff_model_sha256=sha(ff_model),source_cost_sha256=sha(ROOT/'configs/gw5a_arch_cost_v2.json'),source_lut_model_sha256=sha(ROOT/'results/timing/gowin138_model.json'),
        scope='Uniform 521 ps LUT envelope, observed mux hypotheses and DFFRE costs. Other primitive timings remain upstream defaults. No complete pin permutation, load, wire, SDC or hold model; native signoff required.')
    (out/'manifest.json').write_text(json.dumps(record,indent=2)+'\n');return selected


if __name__=='__main__':print(generate(sys.argv[1]))
