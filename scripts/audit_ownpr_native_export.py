"""Fail-closed checks for the initial own-P&R/native-STA/encoder handoff.

This audits the supported scalar slice, not arbitrary device modes or board
functionality. A generated image is never promoted to timing signoff merely
because the native writer returned success.
"""
import argparse,hashlib,json,re
from pathlib import Path
from ownpr_device import Device,DLL_SHA,ROOT
from ownpr_lut_configuration import NativeFrameProjection
from ownpr_frame_integrity import audit as frame_audit
from ownpr_ff_configuration import FF_CONTROLS
from ownpr_timing_numbers import negative_slack
from ownpr_image_storage import load_json,available
from ownpr_image_storage import read_text as read_artifact_text


def sha(path):
    from ownpr_image_storage import sha as artifact_sha
    return artifact_sha(path)


def connectivity_check(design,mapping,actual,bindings):
    cells=design['modules']['top']['cells'];constant_nets={};expected={}
    hard_rows=[r for r in bindings['bindings'] if r['kind']=='HARD']
    hard_types={'DPX9B','SDPX9B','MULTALU27X18'}
    hard_names={n for n,c in cells.items() if c['type'] in hard_types}
    if len(hard_rows)!=len(hard_names) or {r['instance'] for r in hard_rows}!=hard_names:raise ValueError('Incomplete native hard-resource binding')
    if any(r['primitive']!=cells[r['instance']]['type'] or not r['cached_logic_matches'] for r in hard_rows):raise ValueError('Hard native binding identity changed')
    overrides={}
    if hard_names:
        from ownpr_hard_architecture import HardArchitecture
        from ownpr_native_netlist import inactive_clock_projection
        projections=inactive_clock_projection(design,HardArchitecture().contract(design))
        if projections!=mapping.get('hard_inactive_clock_projection'):raise ValueError('Inactive hard clock projection changed')
        overrides={(r['instance'],r['port'],r['index']):r['after'] for r in projections}
    for name,c in cells.items():
        if c['type'] in ('VCC','GND'):
            bit=next(iter(c['connections'].values()))[0]
            constant_nets[mapping['nets'][str(bit)]]=c['type']
    def normalized(net):return constant_nets.get(net,net)
    aliases=bindings['constant_aliases']
    for name,c in cells.items():
        if mapping['instances'][name] in aliases:continue
        for pin,bits in c['connections'].items():
            if len(bits)!=1 and name not in hard_names:raise ValueError('Nonscalar native handoff pin')
            for index,bit in enumerate(bits):
                label=pin if len(bits)==1 else f'{pin}[{index}]'
                bit=overrides.get((name,pin,index),bit)
                expected[name,label]=(normalized(mapping['nets'][str(bit)]),c['port_directions'][pin])
        # Serializer lifts an inverter LUT1 to LUT4 with replicated truth and
        # three existing GND connections to prevent native inverter absorption.
        # The physical 16-bit word is checked below, including independence
        # from all three extra inputs. This exception changes no logic or pins.
        if c['type']=='LUT1' and int(c['parameters']['INIT'],2)==1 and bindings.get('inverter_lut4_serialization',False):
            for i in range(1,4):expected[name,'I'+str(i)]=('GND','input')
    observed={}
    for r in actual:
        key=r['instance'],r['pin']
        if key in observed:raise ValueError('Duplicate native logical pin')
        observed[key]=(normalized(r['net']),{'In':'input','Out':'output'}[r['direction']])
    if observed!=expected:raise ValueError('Native logical connectivity changed')
    luts=[r for r in bindings['bindings'] if r['kind']=='LUT']
    if len(luts)!=len({r['instance'] for r in luts}) or {r['instance'] for r in luts}!={n for n,c in cells.items() if c['type'].startswith('LUT')}:
        raise ValueError('Incomplete native LUT function readback')
    for r in luts:
        c=cells[r['instance']];k=int(c['type'][-1]);init=int(c['parameters']['INIT'],2)
        expected_word=sum(((init>>(i&((1<<k)-1)))&1)<<i for i in range(16))
        if r['init_word']!=expected_word:raise ValueError('Native physical LUT function changed')
    alus=[r for r in bindings['bindings'] if r['kind']=='ALU']
    if len(alus)!=len({r['instance'] for r in alus}) or {r['instance'] for r in alus}!={n for n,c in cells.items() if c['type']=='ALU'}:
        raise ValueError('Incomplete native arithmetic readback')
    if alus:
        from ownpr_carry_macros import contract
        from ownpr_alu_configuration import Mode2Configuration
        device=Device();profiles=contract(design,device)['profiles'];model=Mode2Configuration(device)
        for row in alus:
            profile=profiles[row['instance']]
            if (row['mode']!=profile['mode'] or row['input_profile']!=profile['input_profile'] or not row['cached_logic_matches']
                or row['expected_truth_word']!=model.word(profile['mode'],profile['input_profile'])):
                raise ValueError('Native arithmetic mode/profile changed')
    muxes=[r for r in bindings['bindings'] if r['kind']=='MUX'];expected_muxes={n for n,c in cells.items() if c['type'].startswith('MUX2_LUT')}
    if len(muxes)!=len({r['instance'] for r in muxes}) or {r['instance'] for r in muxes}!=expected_muxes:raise ValueError('Incomplete native mux readback')
    if muxes:
        from ownpr_mux_architecture import children
        located={r['instance']:r for r in bindings['bindings']};drivers={bit:n for n,c in cells.items() for p,bs in c['connections'].items() if c['port_directions'][p]=='output' for bit in bs}
        for row in muxes:
            name=row['instance'];level=int(cells[name]['type'][-1]);r,c,slot=row['site']
            if row['level']!=level or not row['cached_logic_matches']:raise ValueError('Native mux level/cache changed')
            for pin,(kind,child_slot) in zip(('I0','I1'),children(level,slot)):
                source=drivers[cells[name]['connections'][pin][0]]
                if cells[source]['type']!=kind or located[source]['site']!=[r,c,child_slot]:raise ValueError('Dedicated mux physical input is reversed/misplaced')
    return dict(pins=len(expected),lut_functions=len(luts),arithmetic_functions=len(alus),mux_functions=len(muxes),hard_instances=len(hard_rows),constant_aliases=len(aliases),matched=True)


def timing_check(captures,text,design,placement):
    if any(c['type'] in ('DFFCE','DFFPE') for c in design['modules']['top']['cells'].values()):
        raise ValueError('Asynchronous FF timing signoff requires calibrated recovery/removal coverage')
    paired={r['ff'] for r in placement['clusters'].values() if r['ff'] and not r.get('independent_ff_input')};lookup={};direct=0
    for r in captures:
        if r['direction']!='In':continue
        endpoint=r['native_name']+'/'+r['pin']
        if endpoint in lookup:raise ValueError('Duplicate STA endpoint')
        if not r['nodes']:
            if not (r['direct_connection'] and r['instance'] in paired and r['pin']=='D'):
                raise ValueError('Unexplained missing physical STA endpoint')
            lookup[endpoint]=[[0.]*4 for _ in range(3)];direct+=1
        elif len(r['nodes'])==1:
            lookup[endpoint]=r['nodes'][0]['wire_delays']
        else:raise ValueError('Unsupported ambiguous STA endpoint')
    analysis=None;rows=0;errors=[];io_seen=set();reverse={r['native_name']:r['instance'] for r in captures}
    for line in text.splitlines():
        heading=re.match(r'^3\.(?:3|7)\.\d+ (Setup|Hold|Recovery|Removal) Analysis Report',line)
        if heading:analysis=heading[1]
        match=re.match(r'^\s*[-\d.]+\s+([-\d.]+)\s+tNET\s+(RR|RF|FR|FF)\s+\d+\s+\S+\s+(\S+)\s*$',line)
        if match:
            value,transition,name=match.groups();rows+=1
            # Hash-pinned 138C calibration: setup uses slow/max; hold fast/min.
            # Mixed transition arcs are not silently assigned an RR model.
            if analysis not in ('Setup','Hold') or transition not in ('RR','FF') or name not in lookup:
                raise ValueError('Uncovered timing report row: '+line)
            selector=1 if analysis=='Setup' else 2
            slot=(2 if analysis=='Setup' else 0)+(transition=='FF')
            predicted=lookup[name][selector][slot]
            if abs(float(value)-predicted)>0.00051:errors.append(dict(endpoint=name,reported=float(value),predicted=predicted))
        for alias in re.findall(r'(own_i_\d+)/(?:I|O)\b',line):
            if alias in reverse:io_seen.add(reverse[alias])
    if not rows or errors:raise ValueError('Routed STA differs from physical delay queries: '+str(errors[:3]))
    # Default report_timing prints only 25 paths. Endpoint coverage must be
    # checked independently of that display limit; all primary ports are
    # captured below and their complete SDC bounds are checked by constraints.
    def count(label):
        m=re.search(r'<'+re.escape(label)+r'>:(\d+)',text)
        if not m:raise ValueError('Missing timing summary '+label)
        return int(m[1])
    setup=count('Numbers of Setup Violated Endpoints');hold=count('Numbers of Hold Violated Endpoints')
    endpoints=count('Numbers of Endpoints Analyzed')
    if endpoints<=0:raise ValueError('Empty timing analysis')
    fmax=re.search(r'clk\s+[\d.]+\(MHz\)\s+([\d.]+)\(MHz\)',text)
    if not fmax:raise ValueError('Missing native frequency summary')
    names={r['instance']:r['native_name'] for r in captures};cells=design['modules']['top']['cells']
    constants={'0','1'}
    for c in cells.values():
        if c['type'] in ('VCC','GND'):constants.update(next(iter(c['connections'].values())))
    expected=set()
    for name,c in cells.items():
        if c['type'] in FF_CONTROLS:
            expected.add(names[name]+'/D')
            for pin in ('CE',FF_CONTROLS[c['type']]):
                if c['connections'][pin][0] not in constants:expected.add(names[name]+'/'+pin)
        elif c['type']=='OBUF':expected.add(names[name]+'/O')
    coverage={}
    for kind in ('Setup','Hold'):
        section=re.search(r'^3\.1\.[12] '+kind+r' Paths Table\n(.*?)(?=\n3\.)',text,re.S|re.M)
        if not section:raise ValueError('Missing path table')
        paths=re.findall(r'^\s*\d+\s+([-\d.]+)\s+(own_i_\d+/\w+)\s+(own_i_\d+/\w+)\s+',section[1],re.M)
        if {p[2] for p in paths}!=expected or len(paths)!=len(expected):raise ValueError('Incomplete setup/hold endpoint report')
        if sum(negative_slack(p[0]) for p in paths)!=(setup if kind=='Setup' else hold):raise ValueError('Native summary disagrees with complete rounded paths')
        coverage[kind.lower()]=dict(paths=len(paths),worst_slack_ns=min(float(p[0]) for p in paths))
    mpw=re.search(r'^3\.2 Minimum Pulse Width Table\n(.*?)(?=\n3\.)',text,re.S|re.M)
    pulse_rows=re.findall(r'^\s*\d+\s+([-\d.]+)\s+[\d.]+\s+[\d.]+\s+(High|Low) Pulse Width\s+clk\s+(own_i_\d+)\s*$',mpw[1],re.M) if mpw else []
    pulse_expected={(names[name],edge) for name,c in cells.items() if c['type'] in FF_CONTROLS for edge in ('High','Low')}
    if {(p[2],p[1]) for p in pulse_rows}!=pulse_expected or len(pulse_rows)!=len(pulse_expected):raise ValueError('Incomplete pulse width report')
    return dict(report_rows_verified=rows,direct_connections_verified=direct,endpoints_analyzed=endpoints,
        setup_violated_endpoints=setup,hold_violated_endpoints=hold,reported_fmax_mhz=float(fmax[1]),
        setup_hold_passed=setup==0 and hold==0,io_instances_in_displayed_paths=len(io_seen),
        all_setup_hold_endpoints_displayed=True,path_coverage=coverage,pulse_width_checks=len(pulse_rows),
        minimum_pulse_width_slack_ns=min(float(p[0]) for p in pulse_rows),
        minimum_pulse_width_passed=not any(negative_slack(p[0]) for p in pulse_rows),
        scope='Complete supported-slice setup/hold endpoint and pulse-width reports; actual route queries agree with every displayed tNET row.')


def io_check(text,contract):
    rows={}
    for line in text.splitlines():
        if '|' not in line:continue
        fields=[s.strip() for s in line.split('|')];name=fields[0].lstrip('\\')
        if name not in contract['pins']:continue
        if name in rows:raise ValueError('Duplicate I/O report row')
        if len(fields)<18:raise ValueError('Incomplete native I/O report')
        pin=fields[2].split('/')[0]
        if pin!=contract['pins'][name]:raise ValueError('Package pin changed: '+name)
        props=contract['properties'][name]
        # Only the calibrated LVCMOS33 scalar profile is supported here.
        if fields[7]!='LVCMOS33' or fields[9]!='NONE' or fields[16]!='3.3':raise ValueError('I/O electrical settings not applied: '+name)
        if 'DRIVE=8' in props and fields[8]!='8':raise ValueError('Output drive changed: '+name)
        rows[name]=dict(pin=pin,bank=fields[2].split('/')[1],io_type=fields[7],drive=fields[8],pull=fields[9],voltage=fields[16])
    if set(rows)!=set(contract['pins']):raise ValueError('Missing native I/O report rows')
    return dict(ports=len(rows),pins_preserved=True,electrical_report_matched=True,rows=rows)


def audit(folder,placement=ROOT/'results/ownpr_int2float_place_004',partial=None):
    folder=Path(folder);placement=Path(placement);meta=json.loads((folder/'manifest.json').read_text());binding=json.loads((folder/'binding_report.json').read_text())
    if partial is None:partial=folder/'partial_configuration.json' if available(folder/'partial_configuration.json') else ROOT/'results/ownpr_clock_partial_001/partial_configuration.json'
    if 'partial_configuration_sha256' in meta and sha(partial)!=meta['partial_configuration_sha256']:raise ValueError('Configuration witness changed')
    if 'expanded_routes_sha256' in meta and sha(folder/'expanded_routes.json')!=meta['expanded_routes_sha256']:raise ValueError('Expanded route tree changed')
    if meta['status']!='imported' or meta['dll_sha256']!=DLL_SHA or meta['vendor_placement_executed'] or meta['vendor_routing_executed']:
        raise ValueError('Unverified own P&R import')
    for file,key in [('packed.json','packed_sha256'),('placement.json','placement_sha256')]:
        if sha(placement/file)!=meta[key]:raise ValueError('Source changed')
    if not binding['encoding_preserved_parents'] or not binding['encoding_preserved_geometry']:raise ValueError('Native encoder changed own P&R')
    if sha(folder/'probe.vg')!=meta['exported_netlist_sha256']:raise ValueError('Export netlist changed')
    design=json.loads((placement/'packed.json').read_text());placed=json.loads((placement/'placement.json').read_text());mapping=json.loads((folder/'name_map.json').read_text())
    graph=connectivity_check(design,mapping,load_json(folder/'native_connectivity.json'),binding)
    shared_ff=None
    if placed.get('shared_ff_calibration'):
        from audit_shared_ff_route import check as check_shared_ff
        shared_ff=check_shared_ff(design,placed,Device(),load_json(folder/'sta_endpoint_audit.json'),binding)
    hard_readback=None
    if placed.get('hard_clusters'):
        from ownpr_hard_sta import audit_readback
        hard_readback=audit_readback(folder,design,placed,mapping)
    from ownpr_place import constraints
    cst=(folder/'project.cst').read_text();sdc=(folder/'probe.sdc').read_text();contract=constraints(design['modules']['top'],cst,sdc)
    from ownpr_effective_io_constraints import audit as effective_io_audit
    effective_io=effective_io_audit(read_artifact_text(folder/'probe.tr'),sdc)
    io=io_check((folder/'probe.pin.html').read_text(),contract)
    if any(c['type'] in ('ALU','DFFCE','DFFPE','DPX9B','SDPX9B','MULTALU27X18') or c['type'].startswith('MUX2_LUT') for c in design['modules']['top']['cells'].values()):
        from audit_ownpr_supported_timing import audit as supported_timing
        timing=supported_timing(folder,placement)
    else:
        timing=timing_check(load_json(folder/'sta_endpoint_audit.json'),read_artifact_text(folder/'probe.tr'),design,placed)
    independent_ff=None
    if any(r.get('independent_ff_input') for r in placed['clusters'].values()):
        from audit_ownpr_independent_ff import audit as audit_independent
        independent_ff=audit_independent(folder,placement)
    if binding.get('sta_report_returncode')!=0:raise ValueError('STA report failed')
    payload=load_json(partial);device=Device();projection=NativeFrameProjection(device);data=projection.read(folder/'probe.fs');bad=[]
    for bit in payload['bits']:
        key=tuple(bit['tile']+bit['local_bit']);actual=int(data[projection.index(key)])-48
        if actual!=bit['value']:bad.append(dict(bit=key,expected=bit['value'],actual=actual))
    if bad:raise ValueError('Native image does not encode own configuration: '+str(bad[:3]))
    integrity=frame_audit(folder/'probe.fs')
    if integrity['status']!='frame_integrity_verified':raise ValueError('Native frame CRC failed')
    native_state=None
    if (folder/'io_state_after_encoding.json').exists():
        from audit_native_io_configuration import audit as io_configuration_audit
        native_state=io_configuration_audit(folder)
        (folder/'io_configuration_audit.json').write_text(json.dumps(native_state,indent=2))
    constant_routes=None
    if available(folder/'expanded_routes.json'):
        from ownpr_constant_routes import audit as audit_constants
        constant_routes=audit_constants(folder,design,device)
    return dict(status='initial_handoff_checks_passed',connectivity=graph,io=io,timing=timing,effective_io_constraints=effective_io,
        independent_ff_inputs=independent_ff,
        hard_mode_and_pin_readback=hard_readback,
        native_configuration_readback=native_state,
        native_sta_constraints_satisfied=timing['setup_hold_passed'] and timing.get('async_constraints_passed',True) and timing['minimum_pulse_width_passed'],
        configuration_bits_verified=len(payload['bits']),frame_integrity=integrity,
        source_sha256={p.name:sha(p) for p in (folder/'probe.fs',folder/'probe.tr',folder/'project.cst',folder/'probe.sdc',folder/'native_connectivity.json',folder/'sta_endpoint_audit.json',Path(partial))},
        native_final_sta=True,native_bitstream_encoder=True,vendor_placement_executed=False,vendor_routing_executed=False,
        timing_signoff=False,full_bitstream_verified=False,device_programmed=False,
        pending=([] if timing['setup_hold_passed'] else ['setup/hold closure'])+
            ([] if native_state else ['initial IO/GSR configuration readback'])+
            ['remaining source/physical-mode coverage','broader hard-resource support','hardware functional verification'],
        **({'constant_routes':constant_routes} if constant_routes is not None else {}),
        **({'shared_ff_native_bindings':shared_ff} if shared_ff is not None else {}))


if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('folder',type=Path);a=ap.parse_args();result=audit(a.folder)
    (a.folder/'handoff_audit.json').write_text(json.dumps(result,indent=2))
    print(json.dumps({k:v for k,v in result.items() if k not in ('io','frame_integrity','source_sha256')},indent=2))
