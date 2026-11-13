"""Proved LUT truth rebinding onto explicit physical LUT4 input positions.

The audited A/B/C/D delay envelopes rank pins. Small LUTs may be expressed as
LUT4 with unused inputs tied low. This costs one LUT slot and changes no state.
It is an analytical experiment; only native routing establishes an advantage.
"""
from collections import defaultdict,deque
import copy
import itertools
import json
import math
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from native_boundary_cec import functions
from register_control_map import normalize
from gowin_cones import guarded_pin
from dsp_output_register_pack import kept
from run_abc_reproduction import sha


def optimize(source,model_path=None,input_arrivals=None):
    model_path=Path(model_path or ROOT/'results/timing/gowin138_model.json')
    model=json.loads(model_path.read_text());pins=[math.ceil(p['setup_max_ns']*1000) for p in model['physical_lut_inputs']]
    if len(pins)!=4 or [p['internal_pin'] for p in model['physical_lut_inputs']]!=list('ABCD'):
        raise ValueError('Unknown physical LUT pin model')
    fastest=sorted(range(4),key=lambda i:(pins[i],i));design=copy.deepcopy(source);reports=[]
    for mn,m in design['modules'].items():
        if mn.startswith('~') or int(m.get('attributes',{}).get('blackbox','0'),2):continue
        cells=m.get('cells',{});nodes={};arrival={'0':0.,'1':0.};constants={};users=defaultdict(list)
        for r in m.get('ports',{}).values():
            if r['direction'] in ('input','inout'):
                for b in r['bits']:arrival[b]=float((input_arrivals or {}).get(b,0))
        for name,c in cells.items():
            fs=functions(c)
            if fs:
                for ins,o,truth in fs:
                    if isinstance(o,int):nodes[o]=(name,ins,truth)
            else:
                for p,bs in c['connections'].items():
                    if c['port_directions'].get(p) in ('output','inout'):
                        for b in bs:arrival[b]=345. if c['type'].startswith('DFF') else 0.
        indegree={o:len({b for b in ins if b in nodes}) for o,(_,ins,_) in nodes.items()}
        for o,(_,ins,_) in nodes.items():
            for b in set(ins):users[b].append(o)
        queue=deque(sorted(o for o,n in indegree.items() if n==0));order=[]
        while queue:
            o=queue.popleft();order.append(o)
            for child in users[o]:
                indegree[child]-=1
                if indegree[child]==0:queue.append(child)
        if len(order)!=len(nodes):reports.append(dict(module=mn,skipped='combinational_cycle',changes=[]));continue
        protected=set()
        for c in cells.values():
            for p,bs in c['connections'].items():
                if c['port_directions'].get(p)=='input' and guarded_pin(c['type'],p):protected.update(bs)
        todo=list(protected)
        while todo:
            o=todo.pop()
            if o in nodes:
                for b in nodes[o][1]:
                    if b not in protected:protected.add(b);todo.append(b)
        changes=[]
        for o in order:
            name,ins,truth=nodes[o];c=cells[name]
            reduced=[constants.get(b,b) for b in ins];active,tt=normalize(reduced,truth)
            if not active:
                arrival[o]=0.;constants[o]=str(tt&1);continue
            a={b:arrival.get(b,0.) for b in active}
            kind=c['type'];attrs=dict(c.get('attributes',{}))
            if name.startswith(('ng_acd_','ng_sync_')) and int(attrs.get('ng_generated','0'),2):attrs.pop('keep',None)
            eligible=kind in ('LUT1','LUT2','LUT3','LUT4') and set(c['parameters'])=={'INIT'} and int(c.get('attributes',{}).get('ng_generated','0'),2) and o not in protected and not kept(attrs)
            old_delay=max(a[b]+672+(pins[i] if kind.startswith('LUT') else 521) for i,b in enumerate(ins) if b in a)
            if eligible:
                ranked=sorted(active,key=lambda b:(-a[b],str(b)))
                assigned={b:p for b,p in zip(ranked,fastest)}
                after=max(a[b]+672+pins[assigned[b]] for b in active)
                if after<old_delay-1e-6:
                    newpins=['0']*4
                    for b,p in assigned.items():newpins[p]=b
                    newtruth=0
                    for v in range(16):
                        index=sum(((v>>assigned[b])&1)<<i for i,b in enumerate(active))
                        newtruth|=((tt>>index)&1)<<v
                    # Independently compare the actual original and new cell
                    # for every assignment of its distinct nonconstant inputs.
                    original_unique=list(dict.fromkeys(b for b in ins if b not in ('0','1')))
                    for bits in itertools.product((0,1),repeat=len(original_unique)):
                        vals=dict(zip(original_unique,bits));vals.update({'0':0,'1':1})
                        vals.update({b:int(v) for b,v in constants.items()})
                        old_index=sum(vals[b]<<i for i,b in enumerate(ins))
                        new_index=sum(vals[b]<<i for i,b in enumerate(newpins))
                        if (truth>>old_index&1)!=(newtruth>>new_index&1):raise ValueError('Actual LUT permutation proof failed')
                    c.update(type='LUT4',parameters={'INIT':format(newtruth,'016b')},
                             connections={**{f'I{i}':[b] for i,b in enumerate(newpins)},'F':[o]},
                             port_directions={**{f'I{i}':'input' for i in range(4)},'F':'output'})
                    changes.append(dict(instance=name,before_type=kind,after_type='LUT4',before_local_ps=old_delay,after_local_ps=after))
                    arrival[o]=after;continue
            arrival[o]=old_delay
        reports.append(dict(module=mn,changes=changes))
    return design,dict(schema='physical-lut-pin-experiment-v1',model_sha256=sha(model_path),implementation_sha256=sha(Path(__file__)),
        pin_setup_ps=pins,modules=reports,changed=sum(len(m['changes']) for m in reports),
        scope=__doc__,physical_logical_pin_correspondence='Hypothesis to validate through native P&R; analytical envelopes are not routed timing.')
