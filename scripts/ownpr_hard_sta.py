"""Audit actual hard-cell STA matrices and complete clock pulse reports."""
import re
from collections import Counter
from ownpr_image_storage import load_json,sha
from ownpr_image_storage import read_text as read_artifact_text
from ownpr_hard_timing_model import HardCellTiming,profile
from ownpr_timing_endpoints import contract


def audit_readback(folder,design,placed,mapping):
    from ownpr_hard_architecture import HardArchitecture,bits,constants
    model=HardArchitecture();specs=model.contract(design);cells=design['modules']['top']['cells'];ties=constants(design['modules']['top'])
    meta=load_json(folder/'manifest.json');file=folder/'hard_import_readback.json'
    if sha(file)!=meta.get('hard_import_readback_sha256'):raise ValueError('Hard readback capture changed')
    result=load_json(file);rows=result['records']
    if result.get('native_pnr_executed') is not False or len(rows)!=len(specs) or {r['instance'] for r in rows}!=set(specs):raise ValueError('Incomplete independent hard readback')
    total=0
    for row in rows:
        name=row['instance'];cell=cells[name];loc=placed['hard_clusters'][name];spec=specs[name]
        if row['primitive']!=cell['type'] or row['mode_fields']!=model.mode_attributes[profile(cell)]:raise ValueError('Hard physical mode readback differs')
        logical={label:(port,index,bit) for label,port,index,bit in bits(cell)};inactive={p['pin'] for p in spec['inactive']}
        if len(row['pins'])!=len(logical) or {p['pin'] for p in row['pins']}!=set(logical):raise ValueError('Hard readback pin set differs')
        for pin in row['pins']:
            label=pin['pin'];port,index,bit=logical[label];direction=cell['port_directions'][port]
            if pin['direction']!=('In' if direction=='input' else 'Out'):raise ValueError('Hard pin direction changed')
            seen=set(map(tuple,pin['nodes']));template=spec['endpoints'].get(label)
            wanted={(loc['row']+1+r,loc['column']+1+c,i) for r,c,i in template['nodes']} if template else set()
            omitted=not seen and (label in inactive or direction=='input' and bit in ties)
            if len(seen)!=len(pin['nodes']) or seen!=wanted and not omitted:raise ValueError('Hard readback physical endpoint changed')
            if pin['calibrated_omission']!=omitted:raise ValueError('Hard omission reason changed')
            total+=1
    return dict(status='hard_mode_and_pin_readback_verified',instances=len(rows),pin_bits=total,mode_fields=sum(len(r['mode_fields']) for r in rows),initialization_payload_verified=False)


def bram_clock(cell,port):
    if cell['type']=='DPX9B':
        if port.endswith('A'):return 'CLKA'
        if port.endswith('B'):return 'CLKB'
    elif cell['type']=='SDPX9B':
        if port in ('DI','ADA','BLKSELA','CEA'):return 'CLKA'
        if port in ('DO','ADB','BLKSELB','CEB','OCE','RESET'):return 'CLKB'
    raise ValueError('Uncovered BRAM clock/port relation')


def audit_edges(folder,design,mapping):
    meta=load_json(folder/'manifest.json');index_file=folder/'hard_timing_index.json'
    if sha(index_file)!=meta.get('hard_timing_index_sha256'):raise ValueError('Missing/changed own hard timing capture')
    index=load_json(index_file)
    if index.get('vendor_pnr_executed') is not False:raise ValueError('Hard timing capture is not from own implementation')
    model=HardCellTiming();endpoints=contract(design,mapping);cells=design['modules']['top']['cells'];reverse={v:n for n,v in mapping['instances'].items()}
    seen=set();counts=Counter();required_count=0
    for record in index['instances']:
        name=reverse.get(record['instance'])
        if name not in endpoints['hard_instances'] or name in seen:raise ValueError('Unexpected/duplicate hard timing instance')
        seen.add(name);cell=cells[name];file=folder/record['file']
        if sha(file)!=record['sha256']:raise ValueError('Hard timing edges changed')
        data=load_json(file)
        if not data['full_data_pins']:raise ValueError('Hard timing capture is sampled')
        observed={};required=set();calibration=model.profiles[profile(cell)]
        if cell['type']=='MULTALU27X18':
            required=set(calibration['edges'])
            if calibration.get('registered_oreg'):
                loads=Counter(b for c in cells.values() for p,bs in c['connections'].items() if c['port_directions'][p]=='input' for b in bs)
                loads.update(b for p in design['modules']['top']['ports'].values() if p['direction']=='output' for b in p['bits'])
                required={k for k in required if k[2]!=10 or loads[cell['connections']['DOUT'][int(k[1][5:-1])]]}
        else:
            queries={(q['source'],q['destination'],q['selector'],q['flag_a'],q['flag_b']):q for q in data['queries']}
            if len(queries)!=len(data['queries']) or queries!=calibration['queries']:raise ValueError('BRAM cell-library queries differ from independent calibration')
            for port,values in cell['connections'].items():
                if port in ('CLKA','CLKB'):continue
                clock=bram_clock(cell,port);output=cell['port_directions'][port]=='output'
                width=int(cell['parameters']['BIT_WIDTH_'+('0' if clock=='CLKA' else '1')],2)
                for bit_index,bit in enumerate(values):
                    label=port if len(values)==1 else f'{port}[{bit_index}]'
                    if output:
                        if bit_index<width:required.add((clock,label,10))
                    elif bit not in endpoints['constant_bits']:
                        for kind in ((25,27) if port in ('RESET','RESETA','RESETB') else (20,22)):required.add((clock,label,kind))
        for edge in data['actual_edges']:
            key=edge['source'],edge['destination'],edge['kind']
            if key in observed:raise ValueError('Duplicate hard timing edge')
            if cell['type']!='MULTALU27X18' and edge['source']!=bram_clock(cell,edge['destination'].split('[')[0]):raise ValueError('BRAM timing edge belongs to the wrong clock port')
            if model.matrices(cell,*key)!=edge['matrices']:raise ValueError('Hard timing matrix differs from independent calibration')
            observed[key]=edge['matrices'];counts[cell['type']]+=1
        if not required<=set(observed) or cell['type']=='MULTALU27X18' and not calibration.get('registered_oreg') and set(observed)!=required:raise ValueError('Missing required hard timing arcs')
        required_count+=len(required)
    if seen!=endpoints['hard_instances']:raise ValueError('Incomplete own hard instance coverage')
    return dict(status='own_hard_cell_matrices_matched',instances=len(seen),observed_edges=sum(counts.values()),required_dynamic_and_output_edges=required_count,
        edges_by_primitive=dict(counts),independent_sources=model.sources,dsp_legacy_scalar_table_used=False,timing_signoff=False)


def audit_pulses(folder,design,mapping):
    text=read_artifact_text(folder/'probe.tr');spec=contract(design,mapping)
    expected={(alias,pin,edge) for alias,pin in spec['clocks'] for edge in ('High','Low')}
    section=re.search(r'^3\.2 Minimum Pulse Width Table\n(.*?)(?=\n3\.)',text,re.M|re.S)
    rows=re.findall(r'^\s*\d+\s+([-\d.]+)\s+([\d.]+)\s+([\d.]+)\s+(High|Low) Pulse Width\s+clk\s+(own_i_\d+)\s*$',section[1],re.M) if section else []
    if Counter((r[4],r[3]) for r in rows)!=Counter((a,e) for a,p,e in expected):raise ValueError('Incomplete hard/FF pulse summary')
    detail=re.search(r'^3\.4 Minimum Pulse Width Report\n(.*?)(?=\n3\.)',text,re.M|re.S)
    if not detail:raise ValueError('Missing detailed pulse-width report')
    captures={r['native_name']+'/'+r['pin']:r for r in load_json(folder/'sta_endpoint_audit.json') if r['direction']=='In'}
    seen=set();summaries=[];wire_rows=0
    for block in re.split(r'^\s*MPW\d+\s*$',detail[1],flags=re.M)[1:]:
        values={key:re.search(r'^'+key+r':\s*(\S+)',block,re.M) for key in ('Slack','Actual Width','Required Width','Objects')}
        edge=re.search(r'^Type:\s*(High|Low) Pulse Width',block,re.M)
        if any(v is None for v in values.values()) or edge is None:raise ValueError('Incomplete pulse detail')
        slack,actual,required=[float(values[k][1]) for k in ('Slack','Actual Width','Required Width')];alias=values['Objects'][1];edge=edge[1]
        ends=[];arrivals=[]
        for label,selector,transition,slot in [('Late',1,'RR' if edge=='High' else 'FF',2 if edge=='High' else 3),('Early',2,'FF' if edge=='High' else 'RR',1 if edge=='High' else 0)]:
            part=re.search(label+r' clock Path:\n(.*?)(?=\n(?:Late|Early) clock Path:|\Z)',block,re.S)
            wires=re.findall(r'^\s*([-\d.]+)\s+([-\d.]+)\s+tNET\s+(RR|RF|FR|FF)\s+(\S+)\s*$',part[1],re.M) if part else []
            if len(wires)!=1:raise ValueError('Pulse clock wire is absent or ambiguous')
            arrival,delay,rf,endpoint=wires[0];capture=captures.get(endpoint)
            if not capture or len(capture['nodes'])!=1 or rf!=transition or abs(float(delay)-capture['nodes'][0]['wire_delays'][selector][slot])>.00051:raise ValueError('Pulse clock wire differs from native parent-tree query')
            ends.append(endpoint);arrivals.append(float(arrival));wire_rows+=1
        if ends[0]!=ends[1] or not ends[0].startswith(alias+'/'):raise ValueError('Pulse clock endpoint changed between corners')
        key=alias,ends[0].split('/',1)[1],edge
        if key not in expected or key in seen:raise ValueError('Unexpected/duplicate pulse clock')
        seen.add(key)
        if abs(arrivals[1]-arrivals[0]-actual)>.00151 or abs(actual-required-slack)>.00151:raise ValueError('Pulse width/slack arithmetic differs')
        summaries.append((format(slack,'.3f'),format(actual,'.3f'),format(required,'.3f'),edge,alias))
    if seen!=expected or Counter(summaries)!=Counter(tuple(r) for r in rows):raise ValueError('Detailed pulse coverage differs from summary')
    return dict(status='complete_pulse_reports_and_clock_wires_matched',checks=len(seen),clock_wire_rows=wire_rows,
        required_width_source='Native STA library; not an independent pulse-width library model',timing_signoff=False)
