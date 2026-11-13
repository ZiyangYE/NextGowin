"""Record a passing own-P&R implementation for hash-checked frequency replay."""
import argparse,json
from pathlib import Path
from ownpr_device import ROOT
from ownpr_image_storage import load_json,sha
from workspace_budget import reserve
from run_ownpr_pin_closure import metric

def record(base_report,implementation,case):
    base=next(r for r in load_json(base_report)['designs'] if r['design']==case)
    for name,digest in base['selected_source_sha256'].items():
        if sha(ROOT/name)!=digest:raise ValueError('Baseline checkpoint changed')
    result=load_json(implementation/'manifest.json');m=result['metrics']
    if any(result.get(k,True) for k in ('vendor_placement_executed','vendor_routing_executed','open_chip_database_used')):raise ValueError('Checkpoint is not verified own P&R provenance')
    if result['status']!='checkpoint_handoff_complete' or not m['native_constraints_satisfied'] or not m['other_checks_passed'] or m['setup_violations'] or m['hold_violations']:raise ValueError('Implementation has not passed complete timing')
    state={k:Path(v).resolve() for k,v in result['selected'].items()};rel=lambda p:p.resolve().relative_to(ROOT).as_posix()
    native=load_json(state['sta']/'manifest.json');audit=load_json(state['sta']/'handoff_audit.json')
    if metric(state['sta'])!=m:raise ValueError('Recorded metrics differ from audited report')
    if native['status']!='imported' or audit['status']!='initial_handoff_checks_passed' or sha(state['sta']/'handoff_audit.json')!=native['handoff_audit_sha256'] or sha(state['sta']/'probe.tr')!=audit['source_sha256']['probe.tr']:raise ValueError('Native handoff is not unchanged/audited')
    for path,key in ((state['placement']/'packed.json','packed_sha256'),(state['placement']/'placement.json','placement_sha256'),(ROOT/base['constraints']['cst'],'cst_sha256'),(ROOT/base['constraints']['sdc'],'source_sdc_sha256')):
        if sha(path)!=native[key]:raise ValueError('Implementation or constraints differ from STA')
    files=[state['placement']/n for n in ('packed.json','placement.json')]+[state['routes']/n for n in ('routes.json','nets.json','all_nets.json','clock_tree.json')]+[state['sta']/n for n in ('manifest.json','probe.tr','handoff_audit.json')]+[state['wire']/'wire_delays.json']+[ROOT/base['constraints'][n] for n in ('cst','sdc')]
    return dict(source_report_sha256=sha(base_report),designs=[dict(design=case,selected={k:rel(v) for k,v in state.items()},constraints=base['constraints'],metrics=m,selected_source_sha256={rel(p):sha(p) for p in files})])

if __name__=='__main__':
    p=argparse.ArgumentParser()
    for n in ('base-report','implementation','out'):p.add_argument('--'+n,type=Path,required=True)
    p.add_argument('--case',default='vexriscv');a=p.parse_args();reserve(ROOT,1_000_000);result=record(a.base_report,a.implementation,a.case)
    with a.out.open('x') as stream:json.dump(result,stream,indent=2)
    print('Passing checkpoint recorded',a.out)
