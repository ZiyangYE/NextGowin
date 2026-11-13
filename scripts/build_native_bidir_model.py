"""Validate setup/hold DAGs against independently captured native time pairs.

Native boundary requirements and source-clock arrivals are fixed to the exact
placement/clock/constraints. Rewiring existing data nets can reuse them; moving
state, changing clocks, primitives or constraints requires fresh admission.
"""
import argparse,copy,json,math,time,shutil
from pathlib import Path
from ownpr_image_storage import load_json,read_text,sha,available
from ownpr_bidir_timing import analyze
from ownpr_timing_endpoints import contract
from ownpr_cell_timing import NativeCellTiming
from workspace_budget import reserve
from ownpr_device import ROOT


def pairs_by_pin(data):
    result={}
    for row in data['pins']:
        if len(row['groups'])!=1 or row['groups'][0]['kind']!=1:raise ValueError('Multiple timing domains not admitted')
        states=row['groups'][0]['states'];mapped={}
        for s in states:
            if s['flags'] not in (16,17,32,33):raise ValueError('Uncalibrated timing-state flags')
            tr=0 if s['flags']&1 else 1
            if tr in mapped:raise ValueError('Duplicate native transition')
            mapped[tr]=s['pairs']
        if set(mapped)!={0,1}:raise ValueError('Incomplete native transitions')
        result[row['instance']+'/'+row['pin']]=dict(row,transition_pairs=[mapped[i] for i in (0,1)])
        # Binary evidence identifies pair 0 and 1 as setup and hold for data
        # states. Validate the interpretation against the native slack API.
        if all(len(mapped[i])==2 for i in (0,1)):
            for selector in (0,1):
                index=0 if selector else 1
                slacks=[(p['required_ns']-p['arrival_ns'] if selector else p['arrival_ns']-p['required_ns']) for tr in (0,1) for p in [mapped[tr][index]] if abs(p['required_ns'])<1e9 and abs(p['arrival_ns'])<1e9]
                selected=row['selected_slack'][str(selector)]
                if slacks and abs(selected)<1e9 and abs(min(slacks)-selected)>.00001:raise ValueError('Native time-pair/slack semantics mismatch')
    return result


def build(placement,sta,arrival,states):
    meta=load_json(states/'manifest.json');payload=load_json(states/'pin_feedback.json');am=load_json(arrival/'manifest.json')
    if meta['status']!='captured' or meta['capture_sha256']!=sha(states/'pin_feedback.json') or meta['vendor_placement_executed'] or meta['vendor_routing_executed']:raise ValueError('Unverified native state capture')
    for name,h in meta['input_sha256'].items():
        if sha(sta/name)!=h:raise ValueError('Native state source changed '+name)
    for path,h in am['source_sha256'].items():
        if sha(Path(path))!=h:raise ValueError('Arrival graph source changed')
    if sha(arrival/'graph.json')!=am['derived_artifact_sha256']['graph.json']:raise ValueError('Arrival graph changed')
    native=load_json(sta/'manifest.json')
    if native['packed_sha256']!=sha(placement/'packed.json') or native['placement_sha256']!=sha(placement/'placement.json'):raise ValueError('Changed timing placement')
    design=load_json(placement/'packed.json');layout=load_json(placement/'placement.json');cells=design['modules']['top']['cells'];mapping=load_json(sta/'name_map.json');reverse={v:k for k,v in mapping['instances'].items()};rows=pairs_by_pin(payload)
    late=load_json(arrival/'graph.json');early=copy.deepcopy(late);terminals=[{},{}]
    for graph,index in ((late,0),(early,1)):
        for n in graph['seeds']:
            if n not in rows:raise ValueError('Missing native timing seed '+n)
            graph['seeds'][n]=[p[index]['arrival_ns'] for p in rows[n]['transition_pairs']]
            if any(abs(v)>=1e9 for v in graph['seeds'][n]):raise ValueError('Undefined native timing source')
    expected=contract(design,mapping)['synchronous'];observed=set()
    for n,row in rows.items():
        label=row['native_name']+'/'+row['pin']
        if label not in expected:continue
        if n not in late['nodes'] or any(len(p)!=2 for p in row['transition_pairs']):raise ValueError('Endpoint not in the admitted data DAG')
        for index in (0,1):terminals[index][n]=[p[index]['required_ns'] for p in row['transition_pairs']]
        observed.add(label)
    if observed!=expected:raise ValueError('Incomplete independent logical timing coverage')
    points={(r['instance'],r['pin']):r for r in load_json(sta/'sta_endpoint_audit.json')};raw=NativeCellTiming()
    ext=load_json(sta/'extended_timing_queries.json') if available(sta/'extended_timing_queries.json') else dict(alu_cells=[],carry_links=[])
    mux=load_json(sta/'mux_timing_queries.json') if available(sta/'mux_timing_queries.json') else dict(muxes=[])
    if any(c['type']=='ALU' for c in cells.values()) and not ext['alu_cells']:raise ValueError('Missing arithmetic timing calibration')
    if any(c['type'].startswith('MUX2_LUT') for c in cells.values()) and not mux['muxes']:raise ValueError('Missing mux timing calibration')
    if {reverse[r['instance']] for r in ext['alu_cells']}!={n for n,c in cells.items() if c['type']=='ALU'}:raise ValueError('Arithmetic query instance coverage differs')
    if {reverse[r['instance']] for r in mux['muxes']}!={n for n,c in cells.items() if c['type'].startswith('MUX2_LUT')}:raise ValueError('Mux query instance coverage differs')
    from ownpr_hard_architecture import HARD_TYPES
    if {n for n,c in cells.items() if c['type'] in HARD_TYPES}!=set(layout.get('hard_clusters',{})):raise ValueError('Hard primitive/placement coverage differs')
    alu={(reverse[r['instance']],q['source'],q['destination']):q['values'][:2] for r in ext['alu_cells'] for q in r['queries'] if q['selector']==2}
    muxq={(reverse[r['instance']],q['pin']):q['values'][:2] for r in mux['muxes'] for q in r['queries'] if q['selector']==2}
    dedicated={(reverse[r['destination']],'CIN'):r['minimum_ns'] for r in ext['carry_links']}
    dedicated.update({(reverse[r['instance']],q['pin']):q['minimum_ns'] for r in mux['muxes'] for q in r['links']})
    hard={};hard_sources={}
    for r in (load_json(sta/'hard_timing_index.json')['instances'] if layout.get('hard_clusters') else []):
        data=load_json(sta/r['file']);name=reverse[r['instance']]
        if sha(sta/r['file'])!=r['sha256']:raise ValueError('Hard timing capture changed')
        hard_sources[str((sta/r['file']).resolve())]=r['sha256']
        for e in data['actual_edges']:
            if e['kind'] in (20,22,25,27):continue
            fast=e['matrices'][1]
            if len(fast)!=8 or fast[:2]!=fast[2:4]:raise ValueError('Fast hard source-polarity model not admitted')
            hard[name+'/'+e['source'],name+'/'+e['destination']]=fast[:2]
    untimed_io=[]
    for e in early['edges']:
        src,_,pin=e['source'].rpartition('/');dst,_,dp=e['destination'].rpartition('/');kind=e['kind']
        if kind=='wire':
            point=points.get((dst,dp))
            if point and len(point['nodes'])==1:e['delay']=point['nodes'][0]['wire_delays'][2][:2]
            elif (dst,dp) in dedicated:e['delay']=[dedicated[dst,dp]]*2
            elif e['delay']==[0.,0.]:e['delay']=[0.,0.]
            else:raise ValueError('Missing early wire query')
        elif kind=='cell':
            typ=cells[src]['type']
            if typ.startswith('LUT'):e['delay']=list(raw.lut_input(int(pin[1:]),layout['clusters'][src]['slot'],2)[:2])
            elif typ=='ALU':e['delay']=alu[src,pin,dp]
            else:e['delay']=muxq[src,pin]
        elif kind in ('hard_c2q','hard_combinational'):e['delay']=hard[e['source'],e['destination']]
        elif kind=='io_observed_mode':
            # Exact fixed electrical-mode identity-buffer arc observation.
            # Independent internal pin values below are validation targets.
            if e['destination'] not in rows or e['source'] not in rows:
                if e['destination'] in terminals[1]:raise ValueError('Missing timed IO observation')
                e['delay']=[0.,0.];untimed_io.append(e['destination'])
            else:e['delay']=[rows[e['destination']]['transition_pairs'][i][1]['arrival_ns']-rows[e['source']]['transition_pairs'][i][1]['arrival_ns'] for i in (0,1)]
        else:raise ValueError('Unknown early timing arc kind')
    inputs={**am['source_sha256'],**{str((sta/n).resolve()):h for n,h in meta['input_sha256'].items()},**hard_sources}
    inputs.update({str(f.resolve()):sha(f) for f in (placement/'packed.json',placement/'placement.json',sta/'manifest.json',sta/'handoff_audit.json',sta/'name_map.json',sta/'sta_endpoint_audit.json',states/'manifest.json',states/'pin_feedback.json',arrival/'manifest.json',arrival/'graph.json')})
    if layout.get('hard_clusters'):inputs[str((sta/'hard_timing_index.json').resolve())]=sha(sta/'hard_timing_index.json')
    return dict(late=late,early=early,setup_requirements=terminals[0],hold_requirements=terminals[1]),rows,dict(unconstrained_io_arcs=untimed_io,source_sha256=inputs,scope=__doc__)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for n in ('placement','sta','arrival','states','out'):p.add_argument('--'+n,type=Path,required=True)
    a=p.parse_args();reserve(ROOT,30_000_000);start=time.monotonic();model,rows,meta=build(a.placement,a.sta,a.arrival,a.states);a.out.mkdir(exist_ok=False)
    checked={}
    for name,early in (('setup',False),('hold',True)):
        r=analyze(model['early' if early else 'late'],model['hold_requirements' if early else 'setup_requirements'],early)
        errors={k:[] for k in ('arrival','required','slack')}
        for n,row in rows.items():
            if n not in r['arrival'] or any(len(p)!=2 for p in row['transition_pairs']):continue
            for tr,pairs in enumerate(row['transition_pairs']):
                pair=pairs[int(early)];expected=dict(arrival=pair['arrival_ns'],required=pair['required_ns'],slack=pair['arrival_ns']-pair['required_ns'] if early else pair['required_ns']-pair['arrival_ns'])
                for key,v in expected.items():
                    if abs(v)<1e9 and math.isfinite(r[key][n][tr]):errors[key].append(abs(r[key][n][tr]-v))
        checked[name]=dict(wns=r['wns'],tns=r['tns'],violations=r['violations'],comparisons={k:len(v) for k,v in errors.items()},maximum_errors_ns={k:max(v) for k,v in errors.items()})
    valid=all(v<.002 for row in checked.values() for v in row['maximum_errors_ns'].values())
    meta.update(status='validated_setup_hold_feedback' if valid else 'model_mismatch',validation=checked,wall_seconds=time.monotonic()-start,timing_signoff=False,io_early_arc_policy='Fixed-mode observed OBUF identity arcs; not a general IO timing library')
    (a.out/'validation.json').write_text(json.dumps(meta,indent=2))
    if not valid:print(json.dumps(checked,indent=2));raise SystemExit(1)
    payload=json.dumps(model,separators=(',',':'));reserve(ROOT,len(payload.encode())+2_000_000);(a.out/'model.json').write_text(payload);meta['model_sha256']=sha(a.out/'model.json');(a.out/'manifest.json').write_text(json.dumps(meta,indent=2));shutil.copyfile(__file__,a.out/'builder_source.py.txt');shutil.copyfile(ROOT/'scripts/ownpr_bidir_timing.py',a.out/'propagation_source.py.txt');print(json.dumps(checked,indent=2))


if __name__=='__main__':main()
