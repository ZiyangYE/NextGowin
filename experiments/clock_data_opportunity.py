"""Bound common-clock insertion opportunities; no clock edit or native claim."""
import argparse,json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from ownpr_image_storage import load_json,sha
from ownpr_clock_data_model import ClockDataModel
from ownpr_model_reference import load_reference
from ownpr_io_contract import audit
from workspace_budget import reserve


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for n in ('checkpoint','model','out'):p.add_argument('--'+n,type=Path,required=True)
    a=p.parse_args();reserve(ROOT,10000000);row=load_json(a.checkpoint)['designs'][0]
    for path,h in row['selected_source_sha256'].items():
        if sha(ROOT/path)!=h:raise ValueError('Checkpoint changed')
    state={k:ROOT/v for k,v in row['selected'].items()};load_reference(a.model,state['placement'],state['routes'],state['wire'])
    design=load_json(state['placement']/'packed.json');model=ClockDataModel(load_json(a.model/'model.json'),design,load_json(state['sta']/'sta_endpoint_audit.json'))
    contract=audit(design['modules']['top'],(ROOT/row['constraints']['cst']).read_text(),(ROOT/row['constraints']['sdc']).read_text());period=contract['period_ns'];rows=[]
    for step in range(-10,81):
        shift=step*.05;metrics=model.ideal(shift);setup=metrics['setup'];hold=metrics['hold'];denominator=period-setup['wns']
        rows.append(dict(ideal_common_shift_ns=shift,model_fmax_mhz_not_native=1000/denominator if denominator>0 else None,setup_wns_ns=setup['wns'],hold_wns_ns=hold['wns'],hold_violations=hold['violations'],minimum_local_hold_repair_ns=max(0.,-hold['wns']),negative_hold_endpoints={k:v for k,v in hold['endpoints'].items() if v<0}))
    result=dict(status='ideal_clock_data_bound_not_implementation',rows=rows,best_ignoring_hold=max(rows,key=lambda r:r['model_fmax_mhz_not_native'] or 0),
        best_without_hold_repairs=max((r for r in rows if r['hold_violations']==0),key=lambda r:r['model_fmax_mhz_not_native'] or 0),
        actual_source_native_fmax_mhz=row['metrics']['reported_fmax_mhz'],timing_signoff=False,scope='Equal insertion delta at both corners on all captured FFs, no extra clock uncertainty/dispersion. Existing data arcs fixed. Holds must be physically repaired and actual clock route must be measured/encoded/native-STA checked.',
        source_sha256={str(f.resolve()):sha(f) for f in (a.checkpoint,a.model/'model.json',a.model/'manifest.json',state['sta']/'sta_endpoint_audit.json',Path(__file__),ROOT/'scripts/ownpr_clock_data_model.py')})
    with a.out.open('x') as f:json.dump(result,f,indent=2)
    print(json.dumps({k:result[k] for k in ('status','best_ignoring_hold','best_without_hold_repairs')},indent=2))


if __name__=='__main__':main()
