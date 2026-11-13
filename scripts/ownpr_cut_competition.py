"""Competing-boundary ranking proxy with explicit unchanged-wire assumptions."""
import math
from pathlib import Path
from ownpr_image_storage import load_json,sha

def context(folder,placement,sta):
    folder=Path(folder);meta=load_json(folder/'manifest.json')
    if meta['status']!='arrival_feedback_evaluated' or meta['maximum_absolute_difference_ns']>.002:raise ValueError('Unvalidated arrival feedback')
    for path,digest in meta['source_sha256'].items():
        if sha(Path(path))!=digest:raise ValueError('Arrival source changed')
    for path in (placement/'packed.json',placement/'placement.json',sta/'probe.tr'):
        if meta['source_sha256'].get(str(path.resolve()))!=sha(path):raise ValueError('Arrival feedback belongs to another implementation')
    hard=meta.get('hard_arrival_model')
    if hard:
        from ownpr_device import ROOT
        if sha(sta/'hard_timing_index.json')!=hard['index_sha256']:raise ValueError('Hard arrival index changed')
        for row in hard['captures']:
            if sha(ROOT/row['path'])!=row['sha256']:raise ValueError('Hard arrival capture changed')
        for row in hard['independent_sources']:
            if sha(ROOT/row['folder']/'manifest.json')!=row['manifest_sha256']:raise ValueError('Independent hard calibration changed')
    for name,digest in meta.get('derived_artifact_sha256',{}).items():
        if Path(name).name!=name or sha(folder/name)!=digest:raise ValueError('Derived arrival artifact changed')
    return load_json(folder/'arrivals.json'),dict(model_sha256=sha(folder/'manifest.json'),arrivals_sha256=sha(folder/'arrivals.json'),maximum_native_error_ns=meta['maximum_absolute_difference_ns'])

def score_order(boundary,arrivals,wire,intrinsic):
    values=[arrivals[bit]+wire[bit]+intrinsic(i,len(boundary)) for i,bit in enumerate(boundary)]
    if any(not math.isfinite(v) for v in values):raise ValueError('Invalid candidate arrival')
    return max(values),boundary[max(range(len(values)),key=values.__getitem__)],values

def rank(cut,paths,drivers,values,library,mux,root_slot):
    bits=cut['cut_bits'];old=values.get(cut['root']+'/F')
    if old is None or any(v is None for v in old):return None
    incoming={};wire={}
    for bit in bits:
        if bit not in drivers or bit not in paths:return None
        value=values.get('/'.join(drivers[bit]))
        if value is None or any(v is None for v in value):return None
        incoming[bit]=max(value);wire[bit]=paths[bit][1]
    def mux_delay(level,select):
        return max(v for typ,lev,slot in mux.delays if typ==17 and lev==level for pin in (('S0',) if select else ('I0','I1')) for v in mux.delay(typ,lev,slot,pin,1)[2:4])
    def intrinsic(index,width):
        if width<=4:return max(library.lut_input(index,root_slot,1)[2:4])
        delay=max(v for slot in range(8) for v in library.lut_input(index,slot,1)[2:4]) if index<4 else mux_delay(index+1,True)
        for level in range(max(5,index+2),width+1):delay+=mux_delay(level,False)
        return delay
    options=[]
    for late in bits:
        order=[b for b in bits if b!=late]+[late];arrival,winner,branches=score_order(order,incoming,wire,intrinsic)
        options.append(dict(late_bit=late,order=order,predicted_root_arrival_ns=arrival,winning_boundary_bit=winner,branch_arrivals_ns=branches,gain_proxy_ns=max(old)-arrival))
    chosen=min(options,key=lambda r:(r['predicted_root_arrival_ns'],r['late_bit']))
    return dict(selected=chosen,alternatives=sorted(options,key=lambda r:r['predicted_root_arrival_ns']),old_root_arrival_ns=max(old),scope='Boundary arrivals and first wire samples inherited; changed loading/placement unpredicted; not a delay bound or STA acceptance')
