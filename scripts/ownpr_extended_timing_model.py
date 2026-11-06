"""Calibrated type-17 mode-2 ALU and positive-edge asynchronous FF slice.

The reference is a labelled fixed-contract vendor oracle, never our result.
Models reject unobserved slots/modes and do not certify arbitrary boundaries.
"""
from ownpr_image_storage import read_text as read_artifact_text
import json,re
from collections import Counter
from pathlib import Path
from ownpr_device import Device,ROOT,DLL_SHA
from ownpr_cell_timing import NativeCellTiming
from audit_ownpr_native_export import sha
from extract_timing import ROW

REFERENCE=ROOT/'results/native_uart_timing_calibration_003'
REFERENCE_SHA='f345ad326740e40a24e1b95a2956ba5a74de57274eadcb87f0f851de5cd888cb'


def read(p):return json.loads(Path(p).read_text())


def report_checks(text,data):
    alus={r['instance']:r for r in data['alu_cells']};ffs={r['instance']:r for r in data['async_ff']}
    carry={r['destination']:r for r in data['carry_links']};kind=section=previous=arrival=None
    checked=Counter();async_seen=set();carry_seen=set();errors=[]
    for line in text.splitlines():
        heading=re.match(r'^3\.(?:3|7)\.\d+ (Setup|Hold|Recovery|Removal) Analysis Report',line)
        if heading:kind=heading[1];section=None
        if line.startswith('Data Arrival Path:'):section='arrival';previous=arrival=None
        elif line.startswith('Data Required Path:'):section='required';previous=None
        elif line.startswith('Path Statistics:'):section=None;previous=None
        row=ROW.match(line) if kind and section else None
        if not row:continue
        _,value,typ,tr,_,_,endpoint=row.groups();value=float(value);maximum=kind in ('Setup','Recovery');selector=1 if maximum else 2
        if section=='arrival':arrival=(endpoint,tr)
        instance,sep,pin=endpoint.rpartition('/')
        predicted=None;category=None
        if typ=='tINS' and instance in alus:
            if previous is None or previous[0]!=instance:raise ValueError('Missing ALU report input arc')
            queries=[q for q in alus[instance]['queries'] if (q['source'],q['destination'],q['selector'])==(previous[1],pin,selector)]
            if len(queries)!=1 or tr not in ('RR','RF','FR','FF'):raise ValueError('Unknown ALU report transition')
            predicted=queries[0]['values'][(2 if maximum else 0)+int(tr[-1]=='F')];category='alu_cell_arcs'
        elif typ=='tNET' and instance in carry and pin=='CIN':
            r=carry[instance]
            if not r.get('timing_edge_present') or previous!=(r['source'],'COUT'):raise ValueError('Carry report edge does not match actual timing graph')
            predicted=r['maximum_ns' if maximum else 'minimum_ns'];category='carry_net_arcs';carry_seen.add(instance)
        elif kind in ('Recovery','Removal') and typ in ('tSu','tHld'):
            instance=endpoint
            if instance not in ffs or not arrival or arrival[0]!=instance+'/'+ffs[instance]['control'] or arrival[1] not in ('RR','RF','FR','FF'):
                raise ValueError('Async constraint has no matching control endpoint')
            queries=[q for q in ffs[instance]['queries'] if (q['selector'],q['analysis'])==(selector,kind)]
            if len(queries)!=1:raise ValueError('Missing asynchronous constraint query')
            predicted=queries[0]['values'][(2 if maximum else 0)+int(arrival[1][-1]=='F')]*(-1 if maximum else 1)
            category='async_constraints';async_seen.add((kind,instance))
        if category:
            checked[category]+=1
            if abs(value-predicted)>0.00051:errors.append(dict(kind=kind,endpoint=endpoint,observed=value,predicted=predicted,category=category))
        previous=(instance,pin) if sep else None
    if errors:raise ValueError('Extended report mismatch: '+str(errors[:3]))
    if async_seen!={(k,n) for k in ('Recovery','Removal') for n in ffs}:raise ValueError('Incomplete async constraint report')
    if not checked['alu_cell_arcs'] or not checked['carry_net_arcs']:raise ValueError('No ALU/carry report evidence')
    return dict(checked=dict(checked),carry_endpoints_in_report=len(carry_seen),async_endpoints=len(ffs),all_observed_rows_matched=True)


class ExtendedTimingModel:
    def __init__(self,folder=REFERENCE):
        folder=Path(folder);meta=read(folder/'manifest.json');data=read(folder/'extended_timing_queries.json')
        if (meta['status']!='captured' or meta['dll_sha256']!=DLL_SHA or not meta['vendor_placement_executed'] or not meta['vendor_routing_executed']
            or meta['own_pnr_result'] or sha(folder/'extended_timing_queries.json')!=meta['extended_timing_sha256'] or meta['extended_timing_sha256']!=REFERENCE_SHA):
            raise ValueError('Unverified extended timing calibration')
        from audit_native_carry_placement import audit as audit_carry
        geometry=audit_carry(folder,folder/'probe.vg','top');self.device=Device();self.alu={};self.ff={};self.carry={};raw=NativeCellTiming().data
        bindings={r['name']:r for r in read(folder/'primitive_bindings.json')};sources=set();destinations=set()
        for row in data['alu_cells']:
            name=row['instance'];site=row['site'];typ,r,c,slot=site
            if name in sources or bindings[name]['physical_header']!=site or typ!=106 or self.device.grid[r-1][c-1]!=17 or row['mode']!=2:raise ValueError('Unsupported arithmetic calibration site')
            sources.add(name)
            for q in row['queries']:
                key=(row['mode'],slot,q['source'],q['destination'],q['selector']);values=q['values']
                if key in self.alu and self.alu[key]!=values:raise ValueError('Location-dependent ALU timing not covered')
                # Independently captured raw native library; numerical matches
                # alone do not assign row semantics. Dispatch/site observations
                # above supply the pin/mode/slot association.
                group=raw['groups'][raw['id_map'][1]]
                if not any(r[4*q['selector']:4*q['selector']+4]==values for r in group['rows']):raise ValueError('ALU query is absent from raw timing library')
                self.alu[key]=values
        expected={(2,s,a,b,c) for s in range(6) for a in ('I0','I1','I3','CIN') for b in ('SUM','COUT') for c in range(3)}
        if set(self.alu)!=expected or len(sources)!=geometry['cells']:raise ValueError('Incomplete ALU query coverage')
        ffgroup=raw['groups'][raw['id_map'][2]]
        for row in data['async_ff']:
            if not row['clock_positive'] or row['control'] not in ('CLEAR','PRESET'):raise ValueError('Unsupported asynchronous FF edge/control')
            for q in row['queries']:
                rowid=27 if q['analysis']=='Recovery' else 29;values=q['values'];expected=ffgroup['rows'][ffgroup['pin_ids'][rowid]][4*q['selector']:4*q['selector']+4]
                if values!=expected:raise ValueError('Async dispatcher/raw row mismatch')
                key=(row['control'],q['analysis'],q['selector'])
                if key in self.ff and self.ff[key]!=values:raise ValueError('Location-dependent asynchronous constraint')
                self.ff[key]=values
        if set(self.ff)!={(p,k,s) for p in ('CLEAR','PRESET') for k in ('Recovery','Removal') for s in range(3)}:raise ValueError('Incomplete asynchronous query coverage')
        links={(r['source'],r['destination']):r for r in geometry['connections']}
        for row in data['carry_links']:
            key=row['source'],row['destination'];a=row['source_site'][1:];b=row['destination_site'][1:]
            if key not in links or key in destinations or a!=links[key]['source_site'] or b!=links[key]['destination_site'] or not row.get('timing_edge_present'):
                raise ValueError('Unverified dedicated carry timing edge')
            destinations.add(key);kind=links[key]['kind'];value=(row['minimum_ns'],row['maximum_ns'],row['ownership_flag'])
            if kind in self.carry and self.carry[kind]!=value:raise ValueError('Nonuniform carry edge delay')
            self.carry[kind]=value
        if destinations!=set(links) or set(self.carry)!={'within_cfu','east_cfu_bridge'}:raise ValueError('Incomplete carry timing calibration')
        self.reference_report=report_checks(read_artifact_text(folder/'probe.tr'),data);self.sha256=REFERENCE_SHA

    def check_capture(self,data,design,placement,mapping):
        from ownpr_carry_macros import validate
        geom=validate(design,placement,self.device);aliases=mapping['instances'];cells=design['modules']['top']['cells'];seen=set()
        for r in data['alu_cells']:
            name=next((n for n,a in aliases.items() if a==r['instance']),None)
            if name not in placement['arithmetic_clusters'] or name in seen:raise ValueError('Unexpected captured ALU')
            seen.add(name);site=placement['arithmetic_clusters'][name]
            if r['site']!=[106,site['row']+1,site['column']+1,site['slot']] or r['mode']!=site['mode']:raise ValueError('Captured ALU placement/mode changed')
            qseen=set()
            for q in r['queries']:
                key=r['mode'],site['slot'],q['source'],q['destination'],q['selector']
                if key in qseen or self.alu.get(key)!=q['values']:raise ValueError('Own ALU timing differs from calibration')
                qseen.add(key)
            if len(qseen)!=24:raise ValueError('Incomplete own ALU queries')
        if seen!=set(placement['arithmetic_clusters']):raise ValueError('Missing own ALU timing')
        expected={aliases[n]:('CLEAR' if c['type']=='DFFCE' else 'PRESET') for n,c in cells.items() if c['type'] in ('DFFCE','DFFPE')};seen=set()
        ff_sites={aliases[r['ff']]:[50,r['row']+1,r['column']+1,r['slot']] for r in placement['clusters'].values() if r['ff']}
        if placement.get('ioreg_clusters'):
            from ownpr_ioreg_macros import validate as validate_ioreg
            for record in validate_ioreg(design,placement,self.device)['records']:
                ff_sites[aliases[record['instance']]]=[0,record['site'][0],record['site'][1],1]
        for r in data['async_ff']:
            if r['instance'] in seen or expected.get(r['instance'])!=r['control'] or not r['clock_positive']:raise ValueError('Own async FF differs from calibration')
            if r['site']!=ff_sites.get(r['instance']) or r['pin_id']!={'CLEAR':806,'PRESET':807}[r['control']]:raise ValueError('Async FF site/control binding changed')
            seen.add(r['instance']);qseen=set()
            for q in r['queries']:
                key=r['control'],q['analysis'],q['selector']
                if key in qseen or self.ff.get(key)!=q['values']:raise ValueError('Own async constraint query differs from calibration')
                qseen.add(key)
            if len(qseen)!=6:raise ValueError('Incomplete own async queries')
        if seen!=set(expected):raise ValueError('Missing own async FF queries')
        links={(aliases[r['source']],aliases[r['destination']]):r for r in geom['links']};seen=set()
        for r in data['carry_links']:
            key=r['source'],r['destination']
            if key not in links or key in seen or not r.get('timing_edge_present'):raise ValueError('Missing/unexpected actual carry timing edge')
            seen.add(key);link=links[key]
            a=link['source_site'];b=link['destination_site']
            if r['source_site']!=[106,a[0]+1,a[1]+1,a[2]] or r['destination_site']!=[106,b[0]+1,b[1]+1,b[2]]:raise ValueError('Carry timing capture geometry changed')
            if (r['minimum_ns'],r['maximum_ns'],r['ownership_flag'])!=self.carry[link['kind']]:raise ValueError('Own carry edge query differs from calibration')
        if seen!=set(links):raise ValueError('Incomplete own carry timing edges')
        return dict(alu_cells=len(placement['arithmetic_clusters']),alu_query_vectors=24*len(placement['arithmetic_clusters']),async_ff=len(expected),async_query_vectors=6*len(expected),
            carry_edges=len(links),calibration_sha256=self.sha256,model_scope='type 17, mode 2, six-slot/eastward carry and positive-clock CLEAR/PRESET',arbitrary_device_boundary_legality_verified=False)
