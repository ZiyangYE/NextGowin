"""Inspect a hard-resource capture without relaxing the production STA gate."""
import argparse,json,re,sys
from pathlib import Path
from collections import Counter
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from ownpr_image_storage import sha,load_json
from ownpr_image_storage import read_text as read_artifact_text
from ownpr_ff_configuration import FF_CONTROLS
from ownpr_timing_numbers import negative_slack


def audit(folder,placement):
    read=load_json
    meta=read(folder/'manifest.json');bound=read(folder/'binding_report.json')
    if meta['vendor_placement_executed'] or meta['vendor_routing_executed'] or not meta['sta_report_generated'] or bound.get('sta_report_returncode')!=0:
        raise ValueError('Expected a generated own-P&R STA report')
    text=read_artifact_text(folder/'probe.tr');design=read(placement/'packed.json');cells=design['modules']['top']['cells'];mapping=read(folder/'name_map.json');aliases=mapping['instances'];reverse={v:n for n,v in aliases.items()}
    if sha(placement/'packed.json')!=meta['packed_sha256']:raise ValueError('Netlist changed')
    constants={'0','1'}
    for c in cells.values():
        if c['type'] in ('VCC','GND'):constants.update(next(iter(c['connections'].values())))
    known={'0':0,'1':1};identity_proofs={}
    for name,c in cells.items():
        if c['type'] in ('VCC','GND'):
            bit=next(iter(c['connections'].values()))[0];known[bit]=int(c['type']=='VCC');identity_proofs[bit]=[name]
    changed=True
    while changed:
        changed=False
        for name,c in cells.items():
            if c['type']!='LUT1' or int(c['parameters']['INIT'],2)!=2:continue
            source=c['connections']['I0'][0];dest=c['connections']['F'][0]
            if source in known and dest not in known:
                known[dest]=known[source];identity_proofs[dest]=identity_proofs.get(source,[])+[name];changed=True
    expected=set();async_expected=set()
    for name,c in cells.items():
        if c['type'] in FF_CONTROLS:
            expected.add(aliases[name]+'/D')
            if c['connections']['CE'][0] not in constants:expected.add(aliases[name]+'/CE')
            pin=FF_CONTROLS[c['type']]
            if c['connections'][pin][0] not in constants:
                (async_expected if c['type'] in ('DFFCE','DFFPE') else expected).add(aliases[name]+'/'+pin)
        elif c['type']=='OBUF':expected.add(aliases[name]+'/O')
    tables={}
    for i,kind in enumerate(('Setup','Hold','Recovery','Removal'),1):
        section=re.search(r'^3\.1\.'+str(i)+' '+kind+r' Paths Table\n(.*?)(?=\n3\.)',text,re.M|re.S)
        if not section:raise ValueError('Missing endpoint table')
        rows=re.findall(r'^\s*\d+\s+([-\d.]+)\s+(own_i_\d+/\S+)\s+(own_i_\d+/\S+)\s+',section[1],re.M)
        endpoints={r[2] for r in rows};soft=expected if i<=2 else async_expected;extra=endpoints-soft
        tables[kind]=dict(rows=len(rows),unique_endpoints=len(endpoints),missing_soft_endpoints=sorted(soft-endpoints),
            additional_endpoints_by_primitive=dict(Counter(cells[reverse[e.split('/')[0]]]['type'] for e in extra)),
            additional_endpoints=sorted(extra),worst_slack_ns=min((float(r[0]) for r in rows),default=None),violations=sum(negative_slack(r[0]) for r in rows))
    constant_elisions=[];unexplained=[]
    for endpoint in tables['Setup']['missing_soft_endpoints']:
        alias,pin=endpoint.split('/');name=reverse[alias];cell=cells[name];data_pin='I' if cell['type']=='OBUF' and pin=='O' else pin
        bit=cell['connections'][data_pin][0]
        if bit in known:constant_elisions.append(dict(endpoint=endpoint,value=known[bit],identity_chain=identity_proofs.get(bit,[])))
        else:unexplained.append(endpoint)
    hard_expected=set()
    for name,c in cells.items():
        if c['type'] not in ('DPX9B','SDPX9B'):continue
        for pin,values in c['connections'].items():
            if c['port_directions'][pin]!='input' or pin in ('CLKA','CLKB'):continue
            for index,bit in enumerate(values):
                if bit not in known:hard_expected.add(aliases[name]+'/'+(pin if len(values)==1 else f'{pin}[{index}]'))
    hard_coverage={kind:dict(expected=len(hard_expected),missing=sorted(hard_expected-set(tables[kind]['additional_endpoints'])),
        unexpected=sorted(set(tables[kind]['additional_endpoints'])-hard_expected)) for kind in ('Setup','Hold')}
    captures={r['native_name']+'/'+r['pin']:r for r in read(folder/'sta_endpoint_audit.json') if r['direction']=='In'}
    placed=read(placement/'placement.json');paired={r['ff'] for r in placed['clusters'].values() if r['ff']}
    kind=None;ordinary=Counter();uncovered=[];mismatches=[];dedicated=Counter()
    for line in text.splitlines():
        heading=re.match(r'^3\.(?:3|7)\.\d+ (Setup|Hold|Recovery|Removal) Analysis Report',line)
        if heading:kind=heading[1]
        m=re.match(r'^\s*[-\d.]+\s+([-\d.]+)\s+tNET\s+(RR|RF|FR|FF)\s+\d+\s+\S+\s+(\S+)\s*$',line)
        if not m:continue
        value,transition,endpoint=m.groups();alias,pin=endpoint.split('/',1);name=reverse[alias];cell=cells[name]
        if cell['type']=='ALU' and pin=='CIN':dedicated['carry']+=1;continue
        cap=captures.get(endpoint)
        if not cap or kind not in tables or transition not in ('RR','FF'):uncovered.append(line);continue
        nodes=cap['nodes'];maximum=kind in ('Setup','Recovery');selector=1 if maximum else 2;slot=(2 if maximum else 0)+(transition=='FF')
        if len(nodes)==1:predicted=nodes[0]['wire_delays'][selector][slot]
        elif not nodes and cap['direct_connection'] and name in paired and pin=='D':predicted=0.
        else:uncovered.append(line);continue
        if abs(float(value)-predicted)>.00051:mismatches.append(dict(endpoint=endpoint,kind=kind,reported=float(value),queried=predicted))
        ordinary[kind]+=1
    from ownpr_device import Device
    from ownpr_lut_configuration import NativeFrameProjection
    from ownpr_frame_integrity import audit as frame_audit
    partial=read(folder/'partial_configuration.json');projection=NativeFrameProjection(Device());image=projection.read(folder/'probe.fs')
    wrong=[b for b in partial['bits'] if int(image[projection.index(tuple(b['tile']+b['local_bit']))])-48!=b['value']]
    from audit_ownpr_native_export import io_check
    from ownpr_place import constraints
    io=io_check((folder/'probe.pin.html').read_text(),constraints(design['modules']['top'],(folder/'project.cst').read_text(),(folder/'probe.sdc').read_text()))
    first=re.search(r'^3\.3\.1 Setup Analysis Report\n(.*?)(?=^\s*Path2\s*$)',text,re.M|re.S)
    components=Counter()
    if first:
        arrival=re.search(r'Data Arrival Path:\n(.*?)(?=\nData Required Path:)',first[1],re.S)
        if arrival:
            # The arrival block includes the launch clock before tC2Q.
            # Exclude that prefix when reporting the data-path breakdown.
            active=False
            for line in arrival[1].splitlines():
                row=re.match(r'^\s*[-\d.]+\s+([-\d.]+)\s+(tNET|tINS|tIn|tC2Q)\s+',line)
                if not row:continue
                value,typ=row.groups()
                if typ in ('tC2Q','tIn'):active=True
                if active:components[typ]+=float(value)
    fmax=re.search(r'clk\s+[\d.]+\(MHz\)\s+([\d.]+)\(MHz\)',text)
    stats=re.search(r'Arrival Data Path Delay: \(cell:\s*([\d.]+)\s+([\d.]+)%,\s*route:\s*([\d.]+)\s+([\d.]+)%,\s*tC2Q:\s*([\d.]+)\s+([\d.]+)%',text)
    statistics=dict(cell_ns=float(stats[1]),cell_percent=float(stats[2]),wire_ns=float(stats[3]),wire_percent=float(stats[4]),clock_to_q_ns=float(stats[5]),clock_to_q_percent=float(stats[6])) if stats else None
    return dict(status='diagnostic_only_hard_signoff_incomplete',qualified=False,production_audit_error=meta.get('audit_error'),
        reported_fmax_mhz=float(fmax[1]),tables=tables,ordinary_tnet_rows=dict(ordinary),wire_mismatches=mismatches,uncovered_wire_rows=uncovered,
        constant_soft_endpoint_elisions=constant_elisions,unexplained_soft_endpoint_elisions=unexplained,hard_endpoint_inventory=hard_coverage,
        dedicated_rows_not_checked_here=dict(dedicated),worst_setup_data_components_ns=dict(components),partial_configuration_bits=len(partial['bits']),
        worst_setup_statistics=statistics,
        partial_configuration_mismatches=wrong,frame_integrity=frame_audit(folder/'probe.fs')['status'],fixed_io_ports=io['ports'],pins_preserved=io['pins_preserved'],
        hard_cell_arcs_verified=False,hard_pulse_width_verified=False,initialization_bits_verified=False,
        source_sha256={p.name:sha(p) for p in (folder/'probe.tr',folder/'probe.fs',folder/'sta_endpoint_audit.json',folder/'partial_configuration.json')})


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--capture',type=Path,required=True);p.add_argument('--placement',type=Path,required=True);a=p.parse_args()
    result=audit(a.capture,a.placement);(a.capture/'hard_diagnostic_audit.json').write_text(json.dumps(result,indent=2))
    print(json.dumps({k:v for k,v in result.items() if k not in ('tables','partial_configuration_mismatches','source_sha256','uncovered_wire_rows','wire_mismatches')},indent=2))
    print('wire mismatches',len(result['wire_mismatches']),'uncovered',len(result['uncovered_wire_rows']),'configuration mismatches',len(result['partial_configuration_mismatches']))
