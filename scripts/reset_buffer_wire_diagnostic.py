"""Match measured source-to-buffer wires to each buffer's own downstream tree.

Sums are diagnostic bounds over setup_wire selectors, exclude LUT delays, and
are not path STA. Reject shared-site ambiguity rather than guessing a pin.
"""
import argparse,json
from pathlib import Path
from ownpr_device import ROOT
from ownpr_image_storage import load_json,sha
from workspace_budget import reserve

def analyze(run):
    manifest=load_json(run/'placement/manifest.json');cells=load_json(run/'placement/packed.json')['modules']['top']['cells']
    path=run/'implementation/export/wire/wire_delays.json';wire=load_json(path)
    source_bit=cells[manifest['source']]['connections']['O'][0]
    root='net_'+str(source_bit);roots=wire['nets'][root]['sinks'];rows=[]
    entries=manifest.get('added')
    if entries is None:
        layout=load_json(run/'placement/placement.json');entries=[]
        for name,cell in cells.items():
            if cell['type']=='LUT4' and cell['parameters'].get('INIT')=='1010101010101010' and cell['connections'].get('I0')==[source_bit]:
                loc=layout['clusters'][name]
                if loc.get('ff'):raise ValueError('Identity buffer has a paired register')
                entries.append(dict(instance=name,site=[loc['row'],loc['column'],loc['slot']]))
        if len(entries)!=len(roots):raise ValueError('Root includes unsupported or ambiguous buffer loads')
    for entry in entries:
        r,c,s=entry['site'];matches=[sink for sink in roots if sink['node'][:2]==[r+1,c+1]]
        if len(matches)!=1:raise ValueError('Ambiguous source-to-buffer measurement at site')
        name=entry['instance'];bit=cells[name]['connections']['F'][0];subnet=wire['nets']['net_'+str(bit)]
        setup=max(subnet['sinks'],key=lambda sink:sink['setup_wire_ns']);hold=min(subnet['sinks'],key=lambda sink:sink['hold_wire_ns']);head=matches[0]
        rows.append(dict(buffer=name,site=entry['site'],sinks=len(subnet['sinks']),root_setup_wire_ns=head['setup_wire_ns'],regional_max_setup_wire_ns=setup['setup_wire_ns'],combined_setup_wire_bound_ns=head['setup_wire_ns']+setup['setup_wire_ns'],max_setup_endpoint=setup['node'],combined_min_hold_wire_bound_ns=head['hold_wire_ns']+hold['hold_wire_ns']))
    return dict(status='diagnostic_only',lut_delay_included=False,full_sta_acceptance=False,wire_sha256=sha(path),regions=rows)

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--run',type=Path,required=True);p.add_argument('--out',type=Path,required=True);a=p.parse_args()
    reserve(ROOT,1_000_000);result=analyze(a.run)
    with a.out.open('x') as stream:json.dump(result,stream,indent=2)
    print(json.dumps(result,indent=2))
