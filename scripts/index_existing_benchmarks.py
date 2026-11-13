"""Normalize already-audited own-P&R exports into reusable case checkpoints."""
import argparse,json,re
from pathlib import Path
from ownpr_device import ROOT
from ownpr_image_storage import load_json,sha,read_text
from run_ownpr_pin_closure import metric
from workspace_budget import reserve

def entry(case,folder):
    folder=folder.resolve();export=load_json(folder/'manifest.json');artifacts=export['artifacts']
    if export['status']!='verified_handoff_complete' or export['vendor_placement_executed'] or export['vendor_routing_executed']:raise ValueError('Expected completed own implementation export')
    commands={r['stage']:r['command'] for r in export['stages']}
    def argument(stage,key):
        command=commands[stage];return Path(command[command.index('--'+key)+1]).resolve()
    state={k:Path(artifacts[k]).resolve() for k in ('placement','routes','wire')};state.update(sta=Path(artifacts['export']).resolve(),clock_capture=argument('configuration','clock-capture'))
    constraints={k:argument('final_sta_encoder',flag) for k,flag in [('cst','cst'),('sdc','sdc'),('io_reference','io-reference')]}
    metrics=metric(state['sta']);native=load_json(state['sta']/'manifest.json')
    if metrics!=export['metrics'] or not metrics['native_constraints_satisfied'] or not metrics['other_checks_passed'] or metrics['setup_violations'] or metrics['hold_violations']:raise ValueError('Export is not completely timing-qualified')
    for path,key in [(state['placement']/'packed.json','packed_sha256'),(state['placement']/'placement.json','placement_sha256'),(state['routes']/'routes.json','routes_sha256'),(constraints['cst'],'cst_sha256'),(constraints['sdc'],'source_sdc_sha256')]:
        if sha(path)!=native[key]:raise ValueError('Export source bytes changed')
    files=[state['placement']/n for n in ('packed.json','placement.json')]+[state['routes']/n for n in ('routes.json','nets.json','all_nets.json','clock_tree.json')]+[state['sta']/n for n in ('manifest.json','probe.tr','handoff_audit.json')]+[state['wire']/'wire_delays.json',constraints['cst'],constraints['sdc']]
    rel=lambda p:p.relative_to(ROOT).as_posix();clock=re.search(r'create_clock\s+-name\s+clk\s+-period\s+([\d.]+)',read_text(constraints['sdc']))
    if not clock:raise ValueError('Expected one primary clock')
    return dict(design=case,selected={k:rel(v) for k,v in state.items()},constraints={k:rel(v) for k,v in constraints.items()},metrics=metrics,qualified_mhz=1000/float(clock[1]),selected_source_sha256={rel(p):sha(p) for p in files},source_export_sha256=sha(folder/'manifest.json'))

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--export',action='append',required=True,help='case=export directory');p.add_argument('--out',type=Path,required=True);a=p.parse_args();reserve(ROOT,1_000_000);rows=[]
    for text in a.export:
        case,path=text.split('=',1);rows.append(entry(case,Path(path)))
    if len({r['design'] for r in rows})!=len(rows):raise ValueError('Duplicate case')
    with a.out.open('x') as stream:json.dump(dict(status='checked_existing_baselines_not_new_optimization',designs=rows),stream,indent=2)
    print([(r['design'],r['qualified_mhz']) for r in rows])
