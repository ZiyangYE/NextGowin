"""Final native-report audit for the calibrated arithmetic/async slice."""
import json,re
from pathlib import Path
from audit_ownpr_extended_sta import audit as coverage_audit
from ownpr_extended_timing_model import ExtendedTimingModel,report_checks
from audit_ownpr_native_export import sha
from ownpr_ff_configuration import FF_CONTROLS
from ownpr_timing_numbers import negative_slack
from ownpr_image_storage import load_json
from ownpr_image_storage import read_text as read_artifact_text


def audit(folder,placement):
    folder=Path(folder);placement=Path(placement)
    def read(p):return load_json(p)
    meta=read(folder/'manifest.json');queryfile=folder/'extended_timing_queries.json'
    design=read(placement/'packed.json');placed=read(placement/'placement.json');mapping=read(folder/'name_map.json');text=read_artifact_text(folder/'probe.tr')
    extended=any(c['type'] in ('ALU','DFFCE','DFFPE') for c in design['modules']['top']['cells'].values())
    if extended:
        if sha(queryfile)!=meta.get('extended_timing_queries_sha256'):raise ValueError('Missing or changed extended timing capture')
        data=read(queryfile);model=ExtendedTimingModel();queries=model.check_capture(data,design,placed,mapping);arcs=report_checks(text,data)
    else:
        if placed.get('arithmetic_clusters'):raise ValueError('Arithmetic placement has no corresponding ALUs')
        queries=dict(alu_cells=0,async_ff=0,not_applicable=True);arcs=dict(checked={'carry_net_arcs':0},not_applicable=True)
    coverage=coverage_audit(folder,placement)
    mux_queries=mux_report=None
    ioreg=None
    if placed.get('ioreg_clusters'):
        from audit_ownpr_ioreg import audit as audit_ioreg
        ioreg=audit_ioreg(folder,design,placed,mapping)
    if placed.get('mux_clusters'):
        from ownpr_mux_architecture import MuxArchitecture,report_audit
        if sha(folder/'mux_timing_queries.json')!=meta.get('mux_timing_queries_sha256'):raise ValueError('Missing/changed mux timing capture')
        mux_queries=MuxArchitecture().check_capture(read(folder/'mux_timing_queries.json'),design,placed,mapping)
        # Endpoint summaries may select only S0 paths. All dedicated data edges
        # are required above even when they are absent from the worst-path report.
        mux_report=report_audit(folder,require_internal_rows=False,capture_context=(design,placed,mapping))
    cells=design['modules']['top']['cells'];aliases=mapping['instances']
    wide_ff=None
    if any(r.get('wide_ff_root') for r in placed['clusters'].values()):
        from audit_ownpr_wide_ff import audit as audit_wide_ff
        wide_ff=audit_wide_ff(folder,placement)
    hard_timing=hard_pulses=None
    hard=any(c['type'] in ('DPX9B','SDPX9B','MULTALU27X18') for c in cells.values())
    if hard:
        from ownpr_hard_sta import audit_edges,audit_pulses
        hard_timing=audit_edges(folder,design,mapping);hard_pulses=audit_pulses(folder,design,mapping)
    section=re.search(r'^3\.2 Minimum Pulse Width Table\n(.*?)(?=\n3\.)',text,re.M|re.S)
    rows=re.findall(r'^\s*\d+\s+([-\d.]+)\s+[\d.]+\s+[\d.]+\s+(High|Low) Pulse Width\s+clk\s+(own_i_\d+)\s*$',section[1],re.M) if section else []
    expected={(aliases[n],edge) for n,c in cells.items() if c['type'] in FF_CONTROLS for edge in ('High','Low')}
    if not hard and ({(r[2],r[1]) for r in rows}!=expected or len(rows)!=len(expected)):raise ValueError('Incomplete arithmetic/async pulse width report')
    fmax=re.search(r'clk\s+[\d.]+\(MHz\)\s+([\d.]+)\(MHz\)',text)
    if not fmax:raise ValueError('Missing final frequency summary')
    def count(label):
        m=re.search('<'+re.escape(label)+r'>:(\d+)',text)
        if not m:raise ValueError('Missing native timing summary')
        return int(m[1])
    setup=count('Numbers of Setup Violated Endpoints');hold=count('Numbers of Hold Violated Endpoints');paths=coverage['coverage']
    if setup!=paths['Setup']['violating_paths'] or hold!=paths['Hold']['violating_paths']:raise ValueError('Native summary disagrees with complete paths')
    return dict(status='calibrated_extended_timing_checks_passed',report_rows_verified=sum(coverage['ordinary_tnet_rows'].values())+arcs['checked']['carry_net_arcs']+(mux_report['internal_net_rows'] if mux_report else 0)+(ioreg['internal_net_rows'] if ioreg else 0),
        mux_queries=mux_queries,mux_report=mux_report,wide_ff=wide_ff,ioreg=ioreg,hard_timing=hard_timing,hard_pulse_reports=hard_pulses,
        extended_queries=queries,extended_report_arcs=arcs,endpoints_analyzed=count('Numbers of Endpoints Analyzed'),
        setup_violated_endpoints=setup,hold_violated_endpoints=hold,setup_hold_passed=setup==hold==0,
        async_constraints_passed=paths['Recovery']['violating_paths']==paths['Removal']['violating_paths']==0,
        reported_fmax_mhz=float(fmax[1]),all_setup_hold_endpoints_displayed=True,all_recovery_removal_endpoints_displayed=True,
        path_coverage={k.lower():dict(paths=v['endpoints'],worst_slack_ns=v['worst_slack_ns']) for k,v in paths.items()},
        pulse_width_checks=len(rows),minimum_pulse_width_slack_ns=min(float(r[0]) for r in rows),
        minimum_pulse_width_passed=not any(negative_slack(r[0]) for r in rows),
        native_carry_timing_model_matched=True,native_async_constraints_matched=True,timing_signoff=False,
        scope='Calibrated supported-slice final native report; physical boundary legality, full device modes, closure and hardware are separate checks.')
