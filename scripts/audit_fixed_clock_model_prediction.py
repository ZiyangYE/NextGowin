"""Compare changed-data predictions with a fresh, full native timing capture.

Only a single positive primary clock with an otherwise byte-identical SDC is
admitted. This audits feedback accuracy, not signoff or frequency acceptance.
"""
import argparse,copy,json,math,re
from pathlib import Path
from ownpr_device import ROOT
from ownpr_image_storage import load_json,sha
from ownpr_model_reference import validate_reconstruction
from workspace_budget import reserve


def period_delta(before,after):
    pattern=r'^create_clock -name (\w+) -period ([\d.eE+\-]+) \[get_ports \{(\w+)\}\]$'
    values=[];normalized=[]
    for text in (before,after):
        rows=text.splitlines();clocks=[];output=[]
        for line in rows:
            if not line.strip() or line.lstrip().startswith('#'):
                output.append(line);continue
            if ';' in line or '\\' in line:raise ValueError('Compound SDC is unsupported')
            if line.split()[0] not in ('create_clock','set_input_delay','set_output_delay','set_clock_uncertainty'):raise ValueError('Unsupported timing relationship')
            if line.startswith('create_clock'):
                match=re.fullmatch(pattern,line)
                if not match:raise ValueError('Unsupported primary clock')
                period=float(match[2])
                if not math.isfinite(period) or period<=0:raise ValueError('Invalid clock period')
                clocks.append(period);line=line[:match.start(2)]+'<PERIOD>'+line[match.end(2):]
            output.append(line)
        if len(clocks)!=1:raise ValueError('Exactly one primary clock required')
        values.append(clocks[0]);normalized.append(output)
    if normalized[0]!=normalized[1]:raise ValueError('Other SDC content changed')
    return values[1]-values[0]


def verified_model(folder,status):
    meta=load_json(folder/'manifest.json')
    if meta['status']!=status or meta['model_sha256']!=sha(folder/'model.json'):raise ValueError('Unverified timing model')
    for name,h in meta['source_sha256'].items():
        if sha(Path(name))!=h:raise ValueError('Timing source changed '+name)
    return load_json(folder/'model.json'),meta


def clock_seed(design,port):
    module=design['modules']['top'];row=module['ports'][port]
    if row['direction']!='input' or len(row['bits'])!=1:raise ValueError('Primary clock is not a scalar input')
    pads=[n for n,c in module['cells'].items() if c['type']=='IBUF' and c['connections']['I']==row['bits']]
    if len(pads)!=1:raise ValueError('Ambiguous primary clock buffer')
    return pads[0]+'/O'


def audit(reference,rebuilt,captured,old_sdc,new_sdc,old_native,new_native):
    before=load_json(old_native/'manifest.json');after=load_json(new_native/'manifest.json')
    if before['placement_sha256']!=after['placement_sha256'] or before['clock_tree_sha256']!=after['clock_tree_sha256'] or before['cst_sha256']!=after['cst_sha256']:raise ValueError('Moved state/clock/IO boundary')
    for native,sdc in ((before,old_sdc),(after,new_sdc)):
        if native['source_sdc_sha256']!=sha(sdc) or native['vendor_placement_executed'] or native['vendor_routing_executed'] or not native['sta_completed']:raise ValueError('Native provenance mismatch')
    _,refmeta=verified_model(reference,'validated_setup_hold_feedback')
    predicted,pm=verified_model(rebuilt,'rebuilt_fixed_boundary_lut_graph')
    actual,am=verified_model(captured,'validated_setup_hold_feedback')
    for native,meta in ((old_native,refmeta),(new_native,am)):
        if meta['source_sha256'].get(str((native/'manifest.json').resolve()))!=sha(native/'manifest.json'):raise ValueError('Capture belongs to another native result')
    if pm['source_sha256'].get(str((reference/'model.json').resolve()))!=sha(reference/'model.json'):raise ValueError('Prediction has another reference')
    delta=period_delta(old_sdc.read_text(),new_sdc.read_text());predicted=copy.deepcopy(predicted)
    packed=[Path(n) for n in am['source_sha256'] if Path(n).name=='packed.json']
    if len(packed)!=1 or sha(packed[0])!=after['packed_sha256']:raise ValueError('Ambiguous captured design')
    port=re.search(r'^create_clock .*\[get_ports \{(\w+)\}\]$',new_sdc.read_text(),re.M)[1]
    clock=clock_seed(load_json(packed[0]),port)
    # Default create_clock has waveform {0 T/2}. The clock input also appears
    # as a native DAG seed, even where it has no data fanout. Its falling edge
    # must track T/2; do not ignore a mismatching seed or relax node coverage.
    for key in ('late','early'):
        if clock not in predicted[key]['seeds']:raise ValueError('Missing primary-clock seed')
        predicted[key]['seeds'][clock][1]+=delta/2
    for row in predicted['setup_requirements'].values():
        for i,value in enumerate(row):
            if abs(value)<1e9:row[i]=value+delta
    errors={}
    for mode in ('setup','hold'):
        key=mode+'_requirements';x,y=predicted[key],actual[key]
        if set(x)!=set(y):raise ValueError('Endpoint requirement coverage differs')
        if any((abs(a)<1e9)!=(abs(b)<1e9) for n in x for a,b in zip(x[n],y[n])):raise ValueError('Endpoint transition coverage differs')
        errors[mode]=max(abs(a-b) for n in x for a,b in zip(x[n],y[n]))
        if errors[mode]>.002:raise ValueError('Independent required-time mismatch')
    checks=validate_reconstruction(actual,dict(setup=actual['late'],hold=actual['early']),dict(setup=predicted['late'],hold=predicted['early']),tolerance=.002)
    files=[old_sdc,new_sdc,old_native/'manifest.json',new_native/'manifest.json',Path(__file__),ROOT/'scripts/ownpr_model_reference.py',ROOT/'scripts/ownpr_bidir_timing.py']
    for folder in (reference,rebuilt,captured):files.extend((folder/'manifest.json',folder/'model.json'))
    return dict(status='full_native_changed_data_prediction_validated',period_delta_ns=delta,falling_primary_clock_seed=clock,endpoint_requirement_errors_ns=errors,checks=checks,
                source_sha256={str(f.resolve()):sha(f) for f in files},timing_signoff=False,
                scope='Full rebuilt arc topology and all finite node AT/RT/slack transitions, fresh native-calibrated requirements; single fixed clock/state geometry only.')


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for n in ('reference','rebuilt','captured','old-sdc','new-sdc','old-native','new-native','out'):p.add_argument('--'+n,type=Path,required=True)
    a=p.parse_args();reserve(ROOT,2_000_000)
    result=audit(a.reference,a.rebuilt,a.captured,a.old_sdc,a.new_sdc,a.old_native,a.new_native)
    with a.out.open('x') as stream:json.dump(result,stream,indent=2)
    print(json.dumps({k:v for k,v in result.items() if k!='source_sha256'},indent=2))


if __name__=='__main__':main()
