"""Turn audited final STA data paths into bounded placement edge weights."""
from ownpr_image_storage import read_text as read_artifact_text
import json,re
from pathlib import Path
from ownpr_place import sha
from ownpr_timing_numbers import negative_slack


def load(folder,design,relative_window_ns=None,pin_feedback=None):
    folder=Path(folder);meta=json.loads((folder/'manifest.json').read_text());check=json.loads((folder/'handoff_audit.json').read_text())
    if meta['status']!='imported' or not meta['sta_completed'] or meta['vendor_placement_executed'] or meta['vendor_routing_executed']:
        raise ValueError('Placement feedback requires audited own-P&R final STA')
    if meta['handoff_audit_sha256']!=sha(folder/'handoff_audit.json') or check['source_sha256']['probe.tr']!=sha(folder/'probe.tr'):raise ValueError('Placement feedback report changed')
    from ownpr_native_netlist import emit
    hard=None
    if any(c['type'] in ('DPX9B','SDPX9B','MULTALU27X18') for c in design['modules']['top']['cells'].values()):
        from ownpr_hard_architecture import HardArchitecture
        hard=HardArchitecture()
    text,mapping=emit(design,hard=hard)
    if text!=(folder/'probe.vg').read_text() or mapping!=json.loads((folder/'name_map.json').read_text()):raise ValueError('Placement feedback uses a different logical graph')
    if pin_feedback:
        from ownpr_pin_feedback import load as load_pins,placement_priorities
        budgets,info=load_pins(pin_feedback,folder);window=4. if relative_window_ns is None else relative_window_ns
        priorities=placement_priorities(design,budgets,window)
        return priorities,dict(**info,weighted_endpoints=len(priorities),relative_window_ns=window,
            source_report_sha256=sha(folder/'probe.tr'),source='native_internal_pin_slack')
    reverse={v:n for n,v in mapping['instances'].items()};cells=design['modules']['top']['cells'];priorities={};slack=None;arrival=False;setup=False;paths=0
    worst=check['timing']['path_coverage']['setup']['worst_slack_ns']
    if relative_window_ns is not None and not 0<relative_window_ns<=100:raise ValueError('Invalid relative criticality window')
    if relative_window_ns is None and worst>=0 and not check['timing']['setup_violated_endpoints']:return {},dict(paths=0,weighted_endpoints=0,source_report_sha256=sha(folder/'probe.tr'))
    for line in read_artifact_text(folder/'probe.tr').splitlines():
        h=re.match(r'^3\.3\.\d+ (Setup|Hold|Recovery|Removal) Analysis Report',line)
        if h:setup=h[1]=='Setup';arrival=False
        if not setup:continue
        m=re.match(r'^Slack\s*:\s*([-\d.]+)',line)
        if m:slack=float(m[1]);paths+=(slack<worst+relative_window_ns if relative_window_ns is not None else negative_slack(m[1]))
        if line.startswith('Data Arrival Path:'):arrival=True
        elif line.startswith(('Data Required Path:','Path Statistics:')):arrival=False
        m=re.match(r'^\s*[-\d.]+\s+([-\d.]+)\s+tNET\s+\w+\s+\d+\s+\S+\s+(own_i_\d+)/(\w+(?:\[\d+\])?)\s*$',line)
        if not arrival or slack is None or not m:continue
        if relative_window_ns is None and not negative_slack(slack):continue
        if relative_window_ns is not None and slack>=worst+relative_window_ns:continue
        _,alias,pin=m.groups();name=reverse[alias]
        port=pin.split('[',1)[0]
        if port in ('CLK','CLKA','CLKB','CIN'):continue
        if cells[name]['port_directions'].get(port)!='input':raise ValueError('Feedback tNET does not end on a logical input')
        # The analytical placer weights port nets; aggregate bus-bit criticality
        # by maximum, keeping clock insertion out of data-net attraction.
        weight=max(0.,1-(slack-worst)/relative_window_ns) if relative_window_ns is not None else (1. if worst==0 else min(1.,slack/worst))
        key=name,port;priorities[key]=max(priorities.get(key,0),weight)
    if not priorities:raise ValueError('Negative setup report has no supported data-net placement feedback')
    return priorities,dict(paths=paths,weighted_endpoints=len(priorities),source_report_sha256=sha(folder/'probe.tr'),
        relative_window_ns=relative_window_ns,scope='Criticality increases selected logical edge attraction; it is not a predicted or guaranteed timing gain.')
