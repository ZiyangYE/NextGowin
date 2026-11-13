"""Check complete reported endpoint envelopes after a fixed-clock alias ECO."""
import argparse,json,re
from pathlib import Path
from ownpr_device import ROOT
from ownpr_image_storage import load_json,read_text,sha
from ownpr_bidir_timing import analyze
from evaluate_fixed_clock_rewire import evaluate
from workspace_budget import reserve


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for n in ('model','placement','routes','wire','native','out'):p.add_argument('--'+n,type=Path,required=True)
    p.add_argument('--rebuilt-model',type=Path,help='Independently rebuilt fixed-boundary LUT graph directory')
    a=p.parse_args();reserve(ROOT,2_000_000)
    if a.rebuilt_model:
        meta=load_json(a.rebuilt_model/'manifest.json')
        if meta['status']!='rebuilt_fixed_boundary_lut_graph' or meta['model_sha256']!=sha(a.rebuilt_model/'model.json'):raise ValueError('Unverified rebuilt LUT model')
        for path,h in meta['source_sha256'].items():
            if sha(Path(path))!=h:raise ValueError('Rebuilt model input changed')
        native=load_json(a.native/'manifest.json')
        if native['packed_sha256']!=sha(a.placement/'packed.json') or native['placement_sha256']!=sha(a.placement/'placement.json') or native['source_sdc_sha256']!=meta['fixed_constraint_sha256']['sdc'] or native['cst_sha256']!=meta['fixed_constraint_sha256']['cst']:raise ValueError('Native/model boundary contract differs')
        model=load_json(a.rebuilt_model/'model.json');graphs=dict(setup=model['late'],hold=model['early'])
    else:_,graphs,model=evaluate(a.model,a.placement,a.routes,a.wire,True)
    reverse={v:k for k,v in load_json(a.native/'name_map.json')['instances'].items()};text=read_text(a.native/'probe.tr');checks={}
    audit=load_json(a.native/'handoff_audit.json')
    for name,early in (('setup',False),('hold',True)):
        heading='Hold' if early else 'Setup';section=re.search(r'^3\.3\.\d+ '+heading+r' Analysis Report\n(.*?)(?=^3\.|\Z)',text,re.M|re.S)
        if not section:raise ValueError('Missing native '+name+' paths')
        blocks=re.split(r'^\s*Path\d+\s*$',section[1],flags=re.M)[1:];r=analyze(graphs[name],model['hold_requirements' if early else 'setup_requirements'],early)
        seen=set();errors={k:[] for k in ('arrival','required','slack')}
        for block in blocks:
            trace=re.search(r'Data Arrival Path:\n(.*?)(?=\nData Required Path:)',block,re.S)
            if not trace:raise ValueError('Missing data path')
            rows=[m[1] for line in trace[1].splitlines() if (m:=re.match(r'^\s*[-\d.]+\s+[-\d.]+\s+t\w+\s+(?:(?:RR|RF|FR|FF)\s+)?\d+\s+\S+\s+(\S+)\s*$',line))]
            if not rows:raise ValueError('Empty native path')
            alias,sep,pin=rows[-1].partition('/');endpoint=reverse[alias]+sep+pin
            if endpoint in seen:raise ValueError('Duplicate native endpoint')
            seen.add(endpoint)
            arrival=float(re.search(r'^Data Arrival Time\s*:\s*([-\d.]+)',block,re.M)[1]);required=float(re.search(r'^Data Required Time\s*:\s*([-\d.]+)',block,re.M)[1]);slack=float(re.search(r'^Slack\s*:\s*([-\d.]+)',block,re.M)[1])
            transition=min(range(2),key=lambda t:r['slack'][endpoint][t])
            for key,value in (('arrival',arrival),('required',required),('slack',slack)):errors[key].append(abs(r[key][endpoint][transition]-value))
        if seen!=set(r['endpoints']) or len(blocks)!=audit['timing']['path_coverage'][name]['paths']:raise ValueError('Native/model endpoint coverage differs')
        checks[name]=dict(endpoints=len(seen),maximum_errors_ns={k:max(v) for k,v in errors.items()},predicted={k:r[k] for k in ('wns','tns','violations')})
    valid=all(error<.002 for row in checks.values() for error in row['maximum_errors_ns'].values())
    result=dict(status='changed_netlist_endpoint_envelopes_validated' if valid else 'model_mismatch',checks=checks,
                sources={str(f):sha(f) for f in (a.native/'probe.tr',a.native/'handoff_audit.json',a.placement/'packed.json',a.routes/'routes.json',a.wire/'wire_delays.json',a.model/'model.json',Path(__file__))},
                scope='Every reported setup/hold endpoint envelope after data rewiring; not internal transition revalidation or support for moved clocks/new primitives.')
    if a.rebuilt_model:
        result['sources'].update({str(f):sha(f) for f in (a.rebuilt_model/'model.json',a.rebuilt_model/'manifest.json')})
        result['scope']='Every reported setup/hold endpoint envelope after fixed-boundary LUT topology/placement changes; no moved-state/clock or new hard-mode claim.'
    with a.out.open('x') as stream:json.dump(result,stream,indent=2)
    print(json.dumps(dict(status=result['status'],checks=checks),indent=2))
    if not valid:raise SystemExit(1)


if __name__=='__main__':main()
