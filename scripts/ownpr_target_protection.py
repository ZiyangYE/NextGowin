"""Convert historical placement protection to a checked target clock period.

This is a filter against incidental displacement, not timing prediction for
modified logic. Only a single rising-edge full-period constraint contract is
supported; exceptions and generated/multiple clocks require a richer model.
"""
import math,re
from ownpr_image_storage import read_text,sha
from project_timing import section


def protection_window(sdc,report,target_mhz,margin_ns=.5):
    if not math.isfinite(target_mhz) or not 0<target_mhz<=250 or not math.isfinite(margin_ns) or not 0<=margin_ns<=10:
        raise ValueError('Invalid target protection limits')
    commands=[line.strip() for line in sdc.splitlines() if line.strip() and not line.lstrip().startswith('#')]
    allowed={'create_clock','set_input_delay','set_output_delay','set_clock_uncertainty','report_timing','report_min_pulse_width'}
    if any(line.split()[0] not in allowed or ';' in line or '\\' in line for line in commands):
        raise ValueError('Target protection requires a simple single-clock contract without exceptions')
    clocks=[line for line in commands if line.split()[0]=='create_clock']
    if len(clocks)!=1 or not re.fullmatch(r'create_clock\s+-name\s+clk\s+-period\s+([\d.]+)\s+\[get_ports\s+\{?clk\}?\]',clocks[0]):
        raise ValueError('Target protection requires the checked primary rising clock')
    if any(re.search(r'-(?:clock_fall|rise|fall|add_delay)\b',line) for line in commands):raise ValueError('Unsupported edge-specific constraint')
    period=float(re.search(r'-period\s+([\d.]+)',clocks[0])[1])
    if not math.isfinite(period) or period<=0:raise ValueError('Invalid source period')
    for line in commands:
        if line.startswith(('set_input_delay','set_output_delay')) and not re.search(r'-clock\s+clk\b',line):raise ValueError('IO timing uses another clock')
        for clocks_used in re.findall(r'\[get_clocks\s+\{?([^\]}]+)\}?\]',line):
            if clocks_used.strip()!='clk':raise ValueError('Clock uncertainty uses another clock')
    details=section(report,'3.3.1 Setup Analysis Report')
    relations=re.findall(r'^Setup Relationship:\s*([-\d.]+)',details,re.M)
    endpoints=re.findall(r'^\s*\d+\s+[-\d.]+\s+\S+\s+\S+\s+',section(report,'3.1.1 Setup Paths Table'),re.M)
    if not relations or len(relations)!=len(endpoints) or any(abs(float(v)-period)>.0011 for v in relations):
        raise ValueError('Incomplete or non-full-period setup coverage')
    target=1000/target_mhz;shift=period-target;window=margin_ns+max(0.,shift)
    if window>100:raise ValueError('Target protection window exceeds supported range')
    return window,dict(source_period_ns=period,target_period_ns=target,target_mhz=target_mhz,period_reduction_ns=shift,
        protection_margin_ns=margin_ns,effective_source_setup_window_ns=window,checked_setup_endpoints=len(endpoints),
        hold_window_unchanged=True,current_slacks_predicted=False,
        scope='Old pin slack shifted by verified full-period reduction for incidental placement protection; transformed logic and loaded routes still require complete STA.')


def from_capture(sta,target_mhz,margin_ns=.5):
    window,audit=protection_window(read_text(sta/'probe.sdc'),read_text(sta/'probe.tr'),target_mhz,margin_ns)
    audit.update(source_sdc_sha256=sha(sta/'probe.sdc'),source_report_sha256=sha(sta/'probe.tr'))
    return window,audit
