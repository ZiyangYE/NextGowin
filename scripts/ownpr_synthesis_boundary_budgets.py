"""Transport actual fixed-state native timing boundaries into Boolean mapping.

Only named hard endpoints with finite source-matched timing are admitted.
Unknown combinational/hard-box boundaries fail rather than receiving zero AT.
The budgets omit changed ordinary-wire loads; full routed STA remains mandatory.
"""
import math
from pathlib import Path
from collections import defaultdict
from ownpr_device import ROOT
from ownpr_image_storage import load_json,sha
from ownpr_model_reference import load_reference
from ownpr_bidir_timing import analyze


def minimum_required(values):
    if len(values)!=2 or any(not math.isfinite(v) or (abs(v)>=1e9 and v!=1e10) for v in values):raise ValueError('Invalid native setup requirement')
    active=[v for v in values if abs(v)<1e9]
    if not active:raise ValueError('No active native setup transition')
    return min(active)


def constraints(design,network,checkpoint,model_dir):
    packet=load_json(checkpoint)
    if len(packet['designs'])!=1:raise ValueError('Exactly one native feedback design required')
    row=packet['designs'][0]
    for name,h in row['selected_source_sha256'].items():
        if sha(ROOT/name)!=h:raise ValueError('Feedback checkpoint source changed')
    state={k:ROOT/v for k,v in row['selected'].items()}
    _,graphs,model=load_reference(model_dir,state['placement'],state['routes'],state['wire'])
    timing=analyze(graphs['setup'],model['setup_requirements']);reference=load_json(state['placement']/'packed.json')['modules']['top']['cells'];cells=design['modules']['top']['cells']
    drivers={};loads=defaultdict(list)
    for name,c in cells.items():
        for pin,bits in c['connections'].items():
            for bit in bits:
                if c['port_directions'][pin]=='output':drivers[bit]=(name,pin)
                else:loads[bit].append((name,pin))
    def point(name,pin,field):
        if name not in reference or cells[name]['type']!=reference[name]['type'] or cells[name].get('parameters',{})!=reference[name].get('parameters',{}):raise ValueError('Missing or changed primitive timing boundary '+name)
        key=name+'/'+pin;values=timing[field].get(key)
        if values is None:raise ValueError('Uncovered native timing boundary '+key)
        if field=='required':
            minimum_required(values)
            if any(abs(v)>=1e9 for v in values) and key not in model['setup_requirements']:raise ValueError('Uncalibrated inactive transition '+key)
        elif any(not math.isfinite(v) or abs(v)>=1e9 for v in values):raise ValueError('Uncovered native timing boundary '+key)
        return key,values
    inputs={};outputs={};witnesses=dict(inputs={},outputs={})
    for label in network['inputs']:
        if not label.startswith('b'):raise ValueError('Unrecognized variable input')
        bit=int(label[1:])
        if bit not in drivers:raise ValueError('Missing source boundary driver')
        name,pin=drivers[bit];key,values=point(name,pin,'arrival')
        inputs[label]=math.ceil(1000*max(values));witnesses['inputs'][label]=dict(pin=key,arrival_ns=values)
    for label in network['outputs']:
        if not label.startswith('b'):raise ValueError('Constant outputs require separate timing semantics')
        bit=int(label[1:]);destinations=[]
        for name,pin in loads[bit]:
            cell=cells[name]
            if cell['type'].startswith(('LUT','MUX2_LUT')) or cell['type']=='ALU':continue
            key,values=point(name,pin,'required');destinations.append(dict(pin=key,required_ns=values))
        if not destinations:raise ValueError('Observable output lacks a verified native hard endpoint '+label)
        outputs[label]=math.floor(1000*min(minimum_required(d['required_ns']) for d in destinations));witnesses['outputs'][label]=destinations
    if set(inputs)!=set(network['inputs']) or set(outputs)!=set(network['outputs']):raise ValueError('Incomplete native synthesis boundary coverage')
    return dict(units='ps',input_arrivals_ps=inputs,output_required_ps=outputs),dict(status='source_matched_native_state_boundary_budgets',witnesses=witnesses,
        source_sha256={str(p.resolve()):sha(p) for p in (checkpoint,model_dir/'model.json',model_dir/'manifest.json',state['placement']/'packed.json',state['placement']/'placement.json',state['sta']/'manifest.json',Path(__file__))},
        scope='Fixed state and IO clock/electrical context; max RF arrivals and min RF requirements, ps rounded conservatively. New combinational wires/hold require actual P&R.')
