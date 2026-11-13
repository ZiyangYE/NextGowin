"""Independently validate every timing transition after real clock rerouting."""
import argparse,json,math
from pathlib import Path
from ownpr_device import ROOT
from ownpr_image_storage import load_json,sha
from ownpr_clock_data_model import ClockDataModel
from ownpr_model_reference import validate_reconstruction
from workspace_budget import reserve


def audit(reference,captured,old_native,new_native,placement):
    metas=[];models=[]
    for folder,native in ((reference,old_native),(captured,new_native)):
        m=load_json(folder/'manifest.json')
        if m['status']!='validated_setup_hold_feedback' or m['model_sha256']!=sha(folder/'model.json'):raise ValueError('Unvalidated native model')
        for path,h in m['source_sha256'].items():
            if sha(path)!=h:raise ValueError('Model input changed')
        if m['source_sha256'].get(str((native/'manifest.json').resolve()))!=sha(native/'manifest.json'):raise ValueError('Native/model binding differs')
        metas.append(load_json(native/'manifest.json'));models.append(load_json(folder/'model.json'))
    before,after=metas
    for key in ('packed_sha256','placement_sha256','routes_sha256','source_sdc_sha256','cst_sha256'):
        if before[key]!=after[key]:raise ValueError('Clock-only data/state/constraint boundary changed')
    if before['packed_sha256']!=sha(placement/'packed.json') or before['placement_sha256']!=sha(placement/'placement.json'):raise ValueError('Wrong placement')
    for m in metas:
        if m['status']!='imported' or not m['sta_completed'] or m['vendor_placement_executed'] or m['vendor_routing_executed']:raise ValueError('Not own native STA')
    full=ClockDataModel(models[0],load_json(placement/'packed.json'),load_json(old_native/'sta_endpoint_audit.json'))
    rows=[dict(node=r['nodes'][0]['node'],delays_by_selector=r['nodes'][0]['wire_delays']) for r in load_json(new_native/'sta_endpoint_audit.json') if r['pin']=='CLK' and r['instance'] in full.ff]
    deltas=full.deltas(rows);predicted=full.shifted(deltas);actual=models[1];errors={}
    for mode in ('setup','hold'):
        a,b=predicted[mode+'_requirements'],actual[mode+'_requirements']
        if set(a)!=set(b) or any(len(a[n])!=len(b[n]) for n in a):raise ValueError('Required endpoint coverage differs')
        if any((abs(x)<1e9)!=(abs(y)<1e9) for n in a for x,y in zip(a[n],b[n])):raise ValueError('Required transition coverage differs')
        errors[mode]=max(abs(x-y) for n in a for x,y in zip(a[n],b[n]))
        if errors[mode]>.002:raise ValueError('Independent native requirement mismatch')
    checks=validate_reconstruction(actual,dict(setup=actual['late'],hold=actual['early']),dict(setup=predicted['late'],hold=predicted['early']),.002)
    files=[reference/'manifest.json',reference/'model.json',captured/'manifest.json',captured/'model.json',old_native/'manifest.json',new_native/'manifest.json',old_native/'sta_endpoint_audit.json',new_native/'sta_endpoint_audit.json',placement/'packed.json',placement/'placement.json',Path(__file__),ROOT/'scripts/ownpr_clock_data_model.py']
    return dict(status='complete_native_clock_delta_prediction_validated',checks=checks,endpoint_required_errors_ns=errors,clock_deltas_ns=deltas,
        source_sha256={str(f.resolve()):sha(f) for f in files},scope='Every finite rise/fall node AT/RT/slack and all arc topology under unchanged packed/placement/data routes/constraints; not async/multiclock/pulse-width prediction or full configuration proof.',timing_signoff=False)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('reference','captured','old-native','new-native','placement','out'):p.add_argument('--'+name,type=Path,required=True)
    a=p.parse_args();reserve(ROOT,2000000);r=audit(a.reference,a.captured,a.old_native,a.new_native,a.placement)
    with a.out.open('x') as stream:json.dump(r,stream,indent=2)
    print(json.dumps({k:v for k,v in r.items() if k not in ('source_sha256','clock_deltas_ns')},indent=2))
