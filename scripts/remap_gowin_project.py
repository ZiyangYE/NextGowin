"""Remap selected LUT cones in an existing project with cut-boundary CEC."""
import argparse
import hashlib
import json
import math
from pathlib import Path
import re
from gowin_cones import parse,replace,readback,modules
from gowin_blif import read_blif
from constrained_mapping import run,analyze
from run_abc_reproduction import ROOT,cec,sha


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('input',type=Path);ap.add_argument('--modules',nargs='+',default=[])
    ap.add_argument('--module-pattern',help='Full-match regular expression for module definitions')
    ap.add_argument('--period-ps',type=float,default=12500)
    ap.add_argument('--arrival-ps',type=float,default=0)
    ap.add_argument('--area-allowance',type=float,default=.05)
    ap.add_argument('--model',type=Path,default=ROOT/'results/timing/gowin138_model.json')
    ap.add_argument('--out',type=Path,required=True)
    args=ap.parse_args()
    if (not math.isfinite(args.period_ps) or args.period_ps<=0 or not math.isfinite(args.arrival_ps) or args.arrival_ps<0
        or not math.isfinite(args.area_allowance) or args.area_allowance<0 or len(set(args.modules))!=len(args.modules)):
        ap.error('Invalid boundary model, area allowance or duplicate module')
    out=args.out.resolve();out.mkdir(parents=True,exist_ok=False)
    text=args.input.read_bytes().decode('utf-8');initial_hash=sha(args.input)
    names=args.modules+([name for name in modules(text) if re.fullmatch(args.module_pattern,name)] if args.module_pattern else [])
    if not names or len(names)!=len(set(names)):ap.error('No matching modules or duplicate module selections')
    model=json.loads(args.model.read_text());delay=model['lut_envelope_ps']
    result=dict(scope='LUT cut-boundary equivalence in an archived structural project; existing FF/hard-cell statements and other modules preserved. Not RTL/sequential signoff.',
        timing_scope='Uniform LUT delay, artificial local boundary arrivals and deadlines. Full board SDC is applied separately by P&R.',
        input_sha256=initial_hash,model_sha256=sha(args.model),period_ps=args.period_ps,arrival_ps=args.arrival_ps,
        abc_binary_sha256=sha(ROOT/'build/abc/_TEST/abc.exe'),
        source_sha256={p.name:sha(p) for p in [Path(__file__),ROOT/'scripts/gowin_cones.py',ROOT/'scripts/constrained_mapping.py']},
        area_allowance=args.area_allowance,modules=[],status='running')
    def save():(out/'manifest.json').write_text(json.dumps(result,indent=2)+'\n')
    save()
    for name in names:
        parsed=parse(text,name)
        if not parsed['inputs'] or not parsed['outputs'] or not parsed['luts']:
            raise ValueError('Module has no nonempty optimizable cut: '+name)
        folder=out/name;folder.mkdir()
        source=folder/'source.blif';source.write_text(parsed['blif'])
        network=read_blif(source)
        arr={p:args.arrival_ps for p in network['inputs']};req={p:args.period_ps for p in network['outputs']}
        constraint=folder/'constraints.json'
        constraint.write_text(json.dumps(dict(units='ps',default_input_arrival_ps=args.arrival_ps,
            default_output_required_ps=args.period_ps),indent=2)+'\n')
        candidates={'seed':dict(path=source,timing=analyze(network,arr,req,delay),luts=len(parsed['luts']))}
        failures={}
        for strategy in ['baseline','acd','acd_remap']:
            try:
                mapped=run(source,constraint,args.model,folder/strategy,strategy)
                path=folder/strategy/'mapped_logic.blif'
                candidates[strategy]=dict(path=path,timing=mapped['independent_timing'],
                    luts=sum(bool(n['inputs']) for n in read_blif(path)['nodes']))
            except (RuntimeError,ValueError) as error:
                failures[strategy]=str(error)
        cap=len(parsed['luts'])+math.floor(args.area_allowance*len(parsed['luts']))
        eligible={k:v for k,v in candidates.items() if v['luts']<=cap}
        chosen=min(eligible,key=lambda k:(-eligible[k]['timing']['wns_ps'],-eligible[k]['timing']['tns_ps'],eligible[k]['luts'],k!='seed',k))
        row=dict(name=name,original_module_sha256=hashlib.sha256(parsed['body'].encode()).hexdigest(),
            original_luts=len(parsed['luts']),protected_luts=parsed['protected_luts'],opaque_cells=len(parsed['opaque']),
            cut_inputs=len(parsed['inputs']),cut_outputs=len(parsed['outputs']),area_cap=cap,chosen=chosen,failures=failures,
            boundary_names={parsed['names'][p]:p for p in parsed['inputs']+parsed['outputs']},
            candidates={k:dict(luts=v['luts'],wns_ps=v['timing']['wns_ps'],tns_ps=v['timing']['tns_ps'],logic_sha256=sha(v['path'])) for k,v in candidates.items()})
        if chosen!='seed':
            candidate,extracted,preservation=replace(text,parsed,candidates[chosen]['path'],source)
            back=folder/'readback.blif';back.write_text(readback(extracted,parsed))
            proof=cec(ROOT/'build/abc/_TEST/abc.exe',source,back,folder/'readback_cec.log',180)
            if not proof['equivalent']:raise RuntimeError('Re-extracted replacement INITs are not proved equivalent: '+name)
            row.update(readback_cec=proof,preservation=preservation)
            text=candidate
        else:
            row['preservation']='Original module unchanged byte for byte'
        result['modules'].append(row);save()
        print(name,chosen,row['candidates'],flush=True)
    if sha(args.input)!=initial_hash:raise RuntimeError('Original project netlist changed during remapping')
    target=out/'optimized.vg';target.write_bytes(text.encode('utf-8'))
    result.update(output_sha256=sha(target),changed=result['input_sha256']!=sha(target),
        status='complete_with_failures' if any(r['failures'] for r in result['modules']) else 'complete')
    save()


if __name__=='__main__':main()
