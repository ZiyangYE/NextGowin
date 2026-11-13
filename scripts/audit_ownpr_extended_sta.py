"""Diagnostic report coverage and ordinary-wire audit; no carry STA signoff.

Recovery/removal wire corners are compared with independent native queries.
Dedicated carry tNET rows are inventoried separately and never treated as
validated zero-delay wires merely because the native report prints zero.
"""
import argparse,json,re
from pathlib import Path
from collections import Counter
from audit_ownpr_native_export import sha
from ownpr_ff_configuration import FF_CONTROLS
from ownpr_timing_numbers import negative_slack
from ownpr_image_storage import load_json
from ownpr_image_storage import read_text as read_artifact_text


def audit(folder,placement):
    folder=Path(folder);placement=Path(placement)
    def read(p):return load_json(p)
    meta=read(folder/'manifest.json');bound=read(folder/'binding_report.json')
    if not meta['sta_report_generated'] or bound.get('sta_report_returncode')!=0 or meta['vendor_placement_executed'] or meta['vendor_routing_executed']:
        raise ValueError('No completed own-P&R native timing report')
    if sha(placement/'packed.json')!=meta['packed_sha256'] or sha(folder/'probe.sdc')!=meta['sdc_sha256']:raise ValueError('Timing source changed')
    cells=read(placement/'packed.json')['modules']['top']['cells'];aliases=read(folder/'name_map.json')['instances'];text=read_artifact_text(folder/'probe.tr')
    constants={'0','1'}
    for c in cells.values():
        if c['type'] in ('VCC','GND'):constants.update(next(iter(c['connections'].values())))
    from ownpr_timing_endpoints import contract
    endpoint_contract=contract(read(placement/'packed.json'),read(folder/'name_map.json'))
    synchronous=endpoint_contract['synchronous'];asynchronous=endpoint_contract['asynchronous']
    coverage={}
    for index,kind in enumerate(('Setup','Hold','Recovery','Removal'),1):
        section=re.search(r'^3\.1\.'+str(index)+' '+kind+r' Paths Table\n(.*?)(?=\n3\.)',text,re.M|re.S)
        if not section:raise ValueError('Missing '+kind+' table')
        rows=re.findall(r'^\s*\d+\s+([-\d.]+)\s+(own_i_\d+/\S+)\s+(own_i_\d+/\S+)\s+',section[1],re.M)
        expected=synchronous if index<=2 else asynchronous
        observed={r[2] for r in rows}
        optional={e for e,r in endpoint_contract['optional_constants'].items() if r['asynchronous']==(index>2)}
        if not expected<=observed or not observed<=expected|optional or len(rows)!=len(observed):raise ValueError('Incomplete '+kind+' endpoint coverage')
        coverage[kind]=dict(endpoints=len(rows),worst_slack_ns=min(float(r[0]) for r in rows) if rows else None,violating_paths=sum(negative_slack(r[0]) for r in rows))
    captures={r['native_name']+'/'+r['pin']:r for r in read(folder/'sta_endpoint_audit.json') if r['direction']=='In'}
    paired={r['ff'] for r in read(placement/'placement.json')['clusters'].values() if r['ff'] and not r.get('independent_ff_input')}
    carry={aliases[n]+'/CIN' for n,c in cells.items() if c['type']=='ALU' and c['connections']['CIN'][0] not in constants}
    mux_internal={aliases[n]+'/'+p for n,c in cells.items() if c['type'].startswith('MUX2_LUT') for p in ('I0','I1')}
    placed=read(placement/'placement.json');ioreg_internal={aliases[r['iobuf']]+'/I' for r in placed.get('ioreg_clusters',{}).values()}
    kind=None;matched=Counter();carry_rows=[];mux_rows=[];ioreg_rows=[];errors=[]
    for line in text.splitlines():
        heading=re.match(r'^3\.(?:3|7)\.\d+ (Setup|Hold|Recovery|Removal) Analysis Report',line)
        if heading:kind=heading[1]
        row=re.match(r'^\s*[-\d.]+\s+([-\d.]+)\s+tNET\s+(RR|RF|FR|FF)\s+\d+\s+\S+\s+(\S+)\s*$',line)
        if not row:continue
        value,transition,name=row.groups()
        if name in carry:carry_rows.append(dict(analysis=kind,endpoint=name,reported_delay_ns=float(value)));continue
        if name in mux_internal:mux_rows.append(dict(analysis=kind,endpoint=name,reported_delay_ns=float(value)));continue
        if name in ioreg_internal:ioreg_rows.append(dict(analysis=kind,endpoint=name,reported_delay_ns=float(value)));continue
        if name not in captures or kind not in coverage or transition not in ('RR','FF'):raise ValueError('Uncovered ordinary timing row: '+line)
        capture=captures[name];nodes=capture['nodes']
        if len(nodes)==1:
            maximum=kind in ('Setup','Recovery');selector=1 if maximum else 2;slot=(2 if maximum else 0)+int(transition=='FF')
            predicted=nodes[0]['wire_delays'][selector][slot]
        elif not nodes and capture['direct_connection'] and capture['instance'] in paired and capture['pin']=='D':predicted=0.
        else:raise ValueError('Ambiguous/missing ordinary endpoint')
        if abs(float(value)-predicted)>0.00051:errors.append(dict(analysis=kind,endpoint=name,reported=float(value),predicted=predicted))
        matched[kind]+=1
    active_sections={k for k,v in coverage.items() if v['endpoints']}
    if errors or set(matched)!=active_sections:raise ValueError('Ordinary wire corner mismatch: '+str(errors[:3]))
    return dict(status='extended_report_coverage_and_ordinary_wires_matched',coverage=coverage,ordinary_tnet_rows=dict(matched),
        unverified_carry_tnet_rows=carry_rows,unverified_carry_endpoints=sorted(carry),
        unverified_mux_tnet_rows=mux_rows,
        unverified_ioreg_tnet_rows=ioreg_rows,
        source_sha256={p.name:sha(p) for p in (folder/'probe.tr',folder/'sta_endpoint_audit.json',folder/'manifest.json')},
        carry_timing_verified=False,async_cell_checks_calibrated=False,timing_signoff=False,
        scope='Four report endpoint tables and ordinary wire corners only. Carry arcs and async cell recovery/removal constraints still require independent calibration.')


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('folder',type=Path);p.add_argument('--placement',type=Path,required=True);a=p.parse_args();r=audit(a.folder,a.placement)
    (a.folder/'extended_sta_diagnostic.json').write_text(json.dumps(r,indent=2));print(r['status']);print(json.dumps(r['coverage']));print(r['ordinary_tnet_rows'])
