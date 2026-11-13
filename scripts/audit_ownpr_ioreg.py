"""Own fixed-pad output-register binding, mode, dedicated edge and STA audit."""
from ownpr_image_storage import read_text as read_artifact_text
import json
from pathlib import Path
from ownpr_place import sha
from ownpr_ioreg_macros import validate
from ownpr_ioreg_timing import IoregTimingModel
from extract_timing import ROW


def audit(folder,design,placed,mapping):
    folder=Path(folder);meta=json.loads((folder/'manifest.json').read_text());aliases=mapping['instances'];check=validate(design,placed)
    for file in ('ioreg_state.json','ioreg_timing_queries.json'):
        if sha(folder/file)!=meta.get(file.replace('.json','_sha256')):raise ValueError('Missing/changed own I/O-register capture')
    state=json.loads((folder/'ioreg_state.json').read_text());regs={r['instance']:r for r in state['registers_and_iologic']};expected={aliases[r['instance']] for r in check['records']}
    if {n for n,r in regs.items() if r['physical_header'][0]==0}!=expected:raise ValueError('Unexpected I/O-register binding set')
    pairs={};controls={};static_ce=0;static_controls=set();edges=state['output_register_edges']
    if len(edges)!=len(expected) or {r['source'] for r in edges}!=expected:raise ValueError('Missing/duplicate I/O output edge')
    for record in check['records']:
        alias=aliases[record['instance']];target=aliases[record['iobuf']];site=record['site'];reg=regs[alias]
        if reg['physical_header']!=[0,site[0],site[1],1] or not reg['physical_base_logic_matches'] or not reg['typed_and_base_match']:raise ValueError('Output-register physical cache/site mismatch')
        pins={r['pin'].split('.',1)[1]:r for r in reg['pins']}
        if set(pins)!=set(record['pins']) or any(pins[k]['raw_node']!=v for k,v in record['pins'].items()):raise ValueError('Output-register actual endpoint mismatch')
        controls[alias]=record['control_pin']
        if any(pins[k]['native_flags']&0x40 for k in ('CLK','CE',record['control_pin'])):raise ValueError('Unexpected inverted I/O-register control')
        if record['ce_mode']=='one':
            if pins['CE']['native_flags']&1 or pins['CE']['source_site'] is not None:raise ValueError('Constant I/O CE was routed as a physical signal')
            static_ce+=1
        if record.get('control_mode')=='zero':
            control=record['control_pin']
            if pins[control]['native_flags']&1 or pins[control]['source_site'] is not None:raise ValueError('Constant I/O control was physically routed')
            static_controls.add(alias)
        blocks=[b for b in state['iob_blocks'] if b['site']==site and b['iobuf']==target]
        if len(blocks)!=1:raise ValueError('Output I/O block identity changed')
        objects=[r for r in blocks[0]['iologic'] if r['block_offset']==0x28]
        if len(objects)!=1 or objects[0]['reg_refs']!=[dict(offset=0x818,instance=alias)] or objects[0]['attributes']!=record['attributes']:raise ValueError('Output I/O register mode or A/B ownership changed')
        edge=next(e for e in edges if e['source']==alias)
        if edge['destination']!=target or edge['source_site']!=reg['physical_header'] or edge['destination_site']!=[0,*site]:raise ValueError('Output-register edge endpoints differ')
        if not edge['timing_edge_present'] or not edge['source_has_timing_node'] or not edge['destination_has_timing_node'] or edge['minimum_ns']!=0 or edge['maximum_ns']!=0:raise ValueError('Missing/nonzero direct I/O timing edge')
        pairs[target]=alias
    previous=None;rows=0;seen=set();text=read_artifact_text(folder/'probe.tr')
    for line in text.splitlines():
        if line.startswith(('Data Arrival Path:','Data Required Path:','Path Statistics:')):previous=None
        row=ROW.match(line)
        if not row:continue
        _,delay,typ,_,_,_,endpoint=row.groups();name,sep,pin=endpoint.rpartition('/')
        if typ=='tNET' and name in pairs and pin=='I':
            if previous!=(pairs[name],'Q') or float(delay)!=0:raise ValueError('I/O output report differs from actual timing edge')
            rows+=1;seen.add(name)
        previous=(name,pin) if sep else None
    if seen!=set(pairs):raise ValueError('Missing I/O output report paths')
    model=IoregTimingModel();queries=model.check_capture(json.loads((folder/'ioreg_timing_queries.json').read_text()),placed,mapping,controls);report=model.report(text,controls,static_controls)
    return dict(status='own_output_ioreg_handoff_matched',registers=len(expected),attributes_verified=171*len(expected),direct_edges=len(edges),
        internal_net_rows=rows,timing_queries=queries,timing_report=report,static_ce_one_registers=static_ce,fixed_pad_and_block_identity_verified=True,full_bitstream_verified=False)
