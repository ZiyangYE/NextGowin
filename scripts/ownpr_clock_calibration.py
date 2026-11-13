"""Replay clock samples whose complete tree has independent native STA checks."""
import json,math
from pathlib import Path
from ownpr_device import ROOT,DLL_SHA
from ownpr_place import sha
from ownpr_image_storage import load_json


def retained_reference():
    sample=ROOT/'results/ownpr_uart_secondary_clock_003'
    export=ROOT/'results/ownpr_uart_wide_reorder_handoff_002/native'
    placement=ROOT/'results/ownpr_uart_wide_reorder_place_001'
    meta=json.loads((sample/'manifest.json').read_text());em=json.loads((export/'manifest.json').read_text())
    tree_file=sample/'clock_tree.json';data_file=sample/'clock_wire_delays.json'
    partial=load_json(export/'partial_configuration.json')
    if (meta['status']!='clock_candidate_selected' or meta['dll_sha256']!=DLL_SHA or meta['vendor_pnr_executed']
        or sha(tree_file)!=meta['clock_tree.json_sha256'] or sha(data_file)!=meta['clock_wire_delays.json_sha256']
        or sha(tree_file)!=em['clock_tree_sha256'] or sha(export/'clock_tree.json')!=em['clock_tree_sha256']
        or sha(export/'handoff_audit.json')!=em['handoff_audit_sha256']
        or partial['clock_tree_sha256']!=em['clock_tree_sha256']
        or partial['clock_configuration_audit']['clock_tree_sha256']!=em['clock_tree_sha256']):raise ValueError('Retained clock reference provenance changed')
    from audit_ownpr_native_export import audit
    checked=audit(export,placement)
    if not checked['native_final_sta'] or checked['vendor_placement_executed'] or checked['vendor_routing_executed']:raise ValueError('Clock reference lacks independent final STA')
    tree=json.loads(tree_file.read_text());data=json.loads(data_file.read_text())
    parents={tuple(r['node']):r['parent'] for r in tree['parents']};values={tuple(r['node']):r['values_by_selector'] for r in data['nodes']}
    if len(values)!=len(data['nodes']) or set(values)!=set(parents):raise ValueError('Clock sample nodes differ from its complete tree')
    actual={}
    for row in load_json(export/'sta_endpoint_audit.json'):
        if row['pin']!='CLK':continue
        if len(row['nodes'])!=1:raise ValueError('Ambiguous reference clock endpoint')
        node=row['nodes'][0];key=tuple(node['node'])
        if key in actual and actual[key]!=node['wire_delays']:raise ValueError('Inconsistent duplicate reference clock endpoint')
        actual[key]=node['wire_delays']
    if set(actual)!={tuple(r['node']) for r in data['sinks']}:raise ValueError('Clock reference endpoint coverage differs from final STA')
    maximum=0.
    for sink in data['sinks']:
        stored=sink['delays_by_selector'];observed=actual[tuple(sink['node'])]
        if any(len(v)!=3 or any(len(x)!=4 for x in v) for v in (stored,observed)):raise ValueError('Incomplete clock corner vectors')
        if any(not math.isfinite(x) for v in (stored,observed) for row in v for x in row):raise ValueError('Nonfinite clock reference value')
        error=max(abs(stored[s][i]-observed[s][i]) for s in range(3) for i in range(4));maximum=max(maximum,error)
        if error>2e-6:raise ValueError('Clock samples disagree with independent native final-STA endpoint capture')
    rows=[dict(node=list(p),parent=parents[p],values_by_selector=values[p]) for p in sorted(parents)]
    return rows,dict(reference_kind='own_clock_samples_checked_against_native_final_sta',reference_sha256=sha(data_file),sinks=len(actual),maximum_endpoint_rounding_error_ns=maximum,
        source_sha256={str(p):sha(p) for p in (tree_file,data_file,export/'manifest.json',export/'handoff_audit.json',export/'sta_endpoint_audit.json')},
        scope='Validated retained clock tree only; no timing closure or complete device-mode claim.')


def replay(native):
    old=ROOT/'results/native_global_clock_001';file=old/'native_wire_delays.json'
    if file.exists():
        meta=json.loads((old/'manifest.json').read_text())
        if meta['dll_sha256']!=DLL_SHA or sha(file)!=meta['wire_delays_sha256']:raise ValueError('Historical clock calibration changed')
        rows=json.loads(file.read_text())['nodes'];report=dict(reference_kind='historical_vendor_route_queries',reference_sha256=sha(file))
    else:rows,report=retained_reference()
    parents={tuple(r['node']):tuple(r['parent']) if r['parent'] else None for r in rows};pointers=native.resolve_routed_nodes(parents)
    with native.resolved_parent_tree(parents,pointers):
        for row in rows:
            actual=[list(native.delay_pointer(pointers[tuple(row['node'])],s)) for s in range(3)]
            if actual!=row['values_by_selector']:raise ValueError('Standalone clock query differs from independently validated reference')
    return dict(report,nodes=len(rows),mismatch_count=0,matched=True)
