"""Constraint-driven combinational ABC ACD mapping using the Gowin envelope.

Times are normalized into uniform LUT units before ABC. Independent output
analysis uses the original required times because ABC may relax infeasible
deadlines internally. This is setup analysis only, without interconnect/hold.
"""
import argparse
import hashlib
import json
import math
from pathlib import Path
from gowin_blif import read_blif
from run_abc_reproduction import ROOT,invoke,cec,stats,network_sha

TIMING_DIRECTIVES={'.and_gate_delay','.input_arrival','.output_required',
    '.default_input_arrival','.default_output_required','.default_input_drive',
    '.default_output_load','.input_drive','.output_load'}


def logic_only(text):
    return '\n'.join(line for line in text.splitlines()
        if not line.strip() or line.split()[0] not in TIMING_DIRECTIVES)+'\n'


def constraints(data,network):
    allowed={'units','clock_period_ps','default_input_arrival_ps','default_output_required_ps',
             'input_arrivals_ps','output_required_ps'}
    if set(data)-allowed or data.get('units')!='ps':
        raise ValueError('Unknown constraint field or units must be ps')
    if any(not isinstance(data.get(key,{}),dict) for key in ['input_arrivals_ps','output_required_ps']):
        raise ValueError('Per-port timing fields must be dictionaries')
    def finite(value):
        if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value):
            raise ValueError('Timing values must be finite numbers')
        return float(value)
    default_arr=finite(data.get('default_input_arrival_ps',0))
    default_req=data.get('default_output_required_ps',data.get('clock_period_ps'))
    if default_req is None and set(data.get('output_required_ps',{}))!=set(network['outputs']):
        raise ValueError('Every output needs a required time or a default')
    if 'clock_period_ps' in data and finite(data['clock_period_ps'])<=0:
        raise ValueError('Clock period must be positive')
    if set(data.get('input_arrivals_ps',{}))-set(network['inputs']) or set(data.get('output_required_ps',{}))-set(network['outputs']):
        raise ValueError('Timing constraint names an unknown port')
    arr={p:finite(data.get('input_arrivals_ps',{}).get(p,default_arr)) for p in network['inputs']}
    req={p:finite(data.get('output_required_ps',{}).get(p,default_req)) for p in network['outputs']}
    if any(t<0 for t in arr.values()):
        raise ValueError('Negative input arrival times need an explicit time-origin model')
    return arr,req


def annotate(text,arr,req,delay):
    # If_LutDecEval truncates arrivals to integer levels. Round arrivals up and
    # deadlines down before mapping so truncation cannot make them optimistic.
    lines=['.and_gate_delay 1','.default_input_arrival 0 0']
    lines += [f'.input_arrival {p} {math.ceil(t/delay-1e-12)} {math.ceil(t/delay-1e-12)}' for p,t in arr.items()]
    lines += [f'.output_required {p} {math.floor(t/delay+1e-12)} {math.floor(t/delay+1e-12)}' for p,t in req.items()]
    if text.count('.end')!=1:
        raise ValueError('Only a single combinational model is supported')
    return text.replace('.end','\n'.join(lines)+'\n.end')


def analyze(network,arr,req,delay):
    drivers={n['output']:n for n in network['nodes']}
    arrivals=dict(arr)
    detailed=len(network['inputs'])<=16 and len(network['nodes'])<=10000
    path_length={p:{p:0} for p in network['inputs']} if detailed else {}
    predecessors={};supports={}
    for net in network['topological']:
        node=drivers[net];k=len(node['inputs']);truth=node['truth']
        support=[i for i in range(k) if any(((truth>>x)&1)!=((truth>>(x^(1<<i)))&1) for x in range(1<<k))]
        if not support:
            arrivals[net]=0;supports[net]=[]
            if detailed:
                path_length[net]={}
            continue
        selected=[node['inputs'][i] for i in support]
        predecessor=max(selected,key=lambda p:arrivals[p])
        arrivals[net]=arrivals[predecessor]+delay
        supports[net]=selected;predecessors[net]=predecessor
        if detailed:
            distances={}
            for parent in selected:
                for source,distance in path_length[parent].items():
                    distances[source]=max(distances.get(source,0),distance+1)
            path_length[net]=distances
    required=dict(req)
    for net in reversed(network['topological']):
        if net in required:
            for parent in supports[net]:
                required[parent]=min(required.get(parent,math.inf),required[net]-delay)
    def critical_path(net):
        path=[net]
        while net in predecessors:
            net=predecessors[net];path.append(net)
        return list(reversed(path))
    outputs={p:dict(arrival_ps=arrivals[p],required_ps=req[p],slack_ps=req[p]-arrivals[p],
        path_lut_levels=path_length.get(p,{}),critical_path=critical_path(p)) for p in network['outputs']}
    return dict(outputs=outputs,wns_ps=min(v['slack_ps'] for v in outputs.values()),
        tns_ps=sum(min(0,v['slack_ps']) for v in outputs.values()),
        input_required_ps={p:required.get(p) for p in network['inputs']},
        all_input_path_lengths_included=detailed,
        meets_required=all(v['slack_ps']>=-1e-6 for v in outputs.values()))


def verify_annotations(text,arr,req,delay):
    values={};defaults={}
    for line in text.splitlines():
        words=line.split()
        if not words:
            continue
        if words[0] in ('.input_arrival','.output_required'):
            values[words[0],words[1]]=(float(words[2]),float(words[3]))
        elif words[0] in ('.default_input_arrival','.default_output_required'):
            defaults[words[0]]=(float(words[1]),float(words[2]))
    for kind,ports,default in [('.input_arrival',arr,'.default_input_arrival'),('.output_required',req,'.default_output_required')]:
        for port,physical in ports.items():
            actual=values.get((kind,port),defaults.get(default))
            quantized=math.ceil(physical/delay-1e-12) if kind=='.input_arrival' else math.floor(physical/delay+1e-12)
            if actual is None or any(not math.isclose(value,quantized,rel_tol=1e-5,abs_tol=1e-4) for value in actual):
                raise ValueError(f'ABC dropped or changed timing annotation: {kind} {port}')
    return True


def run(input_path,constraint_path,model_path,out,strategy='acd'):
    out.mkdir(parents=True,exist_ok=False)
    model=json.loads(model_path.read_text())
    if model.get('schema')!='gowin138-synthesis-envelope-v1' or model.get('validated_rows')!=317:
        raise ValueError('Unverified timing model schema/coverage')
    delay=model['lut_envelope_ps']
    binary=ROOT/'build/abc/_TEST/abc.exe'
    strategies={'baseline':'dch; if -K 4','acd':'dch; if -Z 4 -K 6',
                'acd_remap':'dch; if -Z 4 -K 6; strash; dch -f; if -K 4'}
    if strategy not in strategies:
        raise ValueError('Unknown mapping strategy')
    source=out/'source.blif'
    loaded,_=invoke(binary,f'read "{input_path.resolve().as_posix()}"; write_blif "{source.as_posix()}"; echo NEXTGOWIN_DONE',out/'read.log',180)
    if loaded['status']!='ok' or not source.exists():
        raise RuntimeError('Input conversion failed')
    # Read only the logical network; all supplied timing comes from the explicit
    # constraint file, not unspecified metadata left in an input netlist.
    text=logic_only(source.read_text());source.write_text(text)
    original=read_blif(source,max_inputs=11)
    arr,req=constraints(json.loads(constraint_path.read_text()),original)
    annotated=out/'annotated.blif';annotated.write_text(annotate(text,arr,req,delay))
    mapped=out/'mapped.blif'
    # The default hierarchy parser silently skips some timing directives.
    # The old flat parser preserves all annotations through strash/dch/if.
    command=f'read_blif -n "{annotated.as_posix()}"; strash; {strategies[strategy]}; print_stats; write_blif "{mapped.as_posix()}"; echo NEXTGOWIN_DONE'
    mapping,text=invoke(binary,command,out/'mapping.log',180)
    if mapping['status']!='ok' or not mapped.exists():
        raise RuntimeError('Constrained mapping failed')
    annotation_check=verify_annotations(mapped.read_text(),arr,req,delay)
    logic=out/'mapped_logic.blif';logic.write_text(logic_only(mapped.read_text()))
    verified=cec(binary,source,logic,out/'cec.log',180)
    if not verified['equivalent']:
        raise RuntimeError('Constrained mapping CEC failed or unproven')
    timing=analyze(read_blif(logic),arr,req,delay)
    result=dict(schema='constrained-acd-gowin-v1',status='satisfied' if timing['meets_required'] else 'violated',
        scope='Combinational setup mapping with uniform Gowin LUT envelope. No routing, skew, hold, or general SDC interpretation.',
        strategy=strategy,
        input_sha256=hashlib.sha256(input_path.read_bytes()).hexdigest(),
        constraint_sha256=hashlib.sha256(constraint_path.read_bytes()).hexdigest(),
        model_sha256=hashlib.sha256(model_path.read_bytes()).hexdigest(),
        delay_ps=delay,normalization='Input times rounded up and required times down into integer LUT levels; .and_gate_delay=1; independent analysis uses original ps',
        input_arrivals_ps=arr,output_required_ps=req,mapping=mapping,cec=verified,
        annotations_preserved=annotation_check,
        native_relaxed_deadlines='Required times are exceeded' in text,
        independent_timing=timing)
    (out/'manifest.json').write_text(json.dumps(result,indent=2)+'\n')
    return result


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('input',type=Path)
    ap.add_argument('--constraints',type=Path,required=True)
    ap.add_argument('--model',type=Path,default=ROOT/'results/timing/gowin138_model.json')
    ap.add_argument('--out',type=Path,required=True)
    ap.add_argument('--strategy',choices=['baseline','acd','acd_remap'],default='acd')
    args=ap.parse_args()
    result=run(args.input,args.constraints,args.model,args.out.resolve(),args.strategy)
    print(json.dumps(result['independent_timing'],indent=2))
    if not result['independent_timing']['meets_required']:
        raise SystemExit(2)


if __name__=='__main__':
    main()
