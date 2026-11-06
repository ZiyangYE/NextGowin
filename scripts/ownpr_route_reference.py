"""Admit a reviewed own-route reference independently of the logical baseline.

Only physical data trees are offered to the incremental router. It must check
current graph legality, source/sink signatures and reservations again. The
reference supplies neither logic, current timing, nor a replacement clock.
"""
import re
from pathlib import Path
from ownpr_device import ROOT,DLL_SHA
from ownpr_image_storage import load_json,read_text,sha


def check_boundary(base_design,base_layout,seed_design,seed_layout):
    if base_design['modules']['top']['ports']!=seed_design['modules']['top']['ports']:raise ValueError('Route reference changes the logical IO contract')
    if base_layout['fixed_io']!=seed_layout['fixed_io'] or base_layout.get('hard_clusters')!=seed_layout.get('hard_clusters'):
        raise ValueError('Route reference changes fixed IO or hard-resource placement')


def clock_normalized(text):
    result,count=re.subn(r'(create_clock\s+-name\s+clk\s+-period\s+)[\d.]+',lambda m:m[1]+'<period>',text)
    if count!=1:raise ValueError('Route reference requires the checked primary clock contract')
    return result


def select(report,case,base_state,contract,root=ROOT):
    root=Path(root).resolve();report=Path(report).resolve();review=load_json(report);row=next(r for r in review['designs'] if r['design']==case)
    def path(value):
        p=(root/value).resolve()
        if not p.is_relative_to(root):raise ValueError('Route reference leaves workspace')
        return p
    for name,digest in row['selected_source_sha256'].items():
        if sha(path(name))!=digest:raise ValueError('Route reference artifact changed: '+name)
    state={k:path(v) for k,v in row['selected'].items()};constraints={k:path(v) for k,v in row['constraints'].items()}
    if sha(constraints['cst'])!=sha(contract['cst']) or constraints['io_reference']!=Path(contract['io_reference']).resolve():raise ValueError('Route reference uses different IO constraints/reference')
    if clock_normalized(read_text(constraints['sdc']))!=clock_normalized(read_text(contract['sdc'])):raise ValueError('Route reference changes timing constraints beyond period')
    check_boundary(load_json(base_state['placement']/'packed.json'),load_json(base_state['placement']/'placement.json'),load_json(state['placement']/'packed.json'),load_json(state['placement']/'placement.json'))
    rm=load_json(state['routes']/'manifest.json');nm=load_json(state['sta']/'manifest.json')
    if rm['status']!='routed' or rm['vendor_pnr_executed'] or nm['vendor_placement_executed'] or nm['vendor_routing_executed'] or nm['dll_sha256']!=DLL_SHA:
        raise ValueError('Route reference is not a checked own implementation')
    checks={'routes_sha256':state['routes']/'routes.json','packed_sha256':state['placement']/'packed.json','placement_sha256':state['placement']/'placement.json','cst_sha256':constraints['cst'],'source_sdc_sha256':constraints['sdc']}
    if any(nm[k]!=sha(p) for k,p in checks.items()):raise ValueError('Route reference STA does not match physical artifacts')
    return state['routes'],dict(report_sha256=sha(report),route_manifest_sha256=sha(state['routes']/'manifest.json'),routes_sha256=sha(state['routes']/'routes.json'),nets_sha256=sha(state['routes']/'nets.json'),
        source_routes=state['routes'].relative_to(root).as_posix(),reference_metrics=row['metrics'],logic_and_clock_from_primary_checkpoint=True,
        scope='Own data-route seed only. Current endpoint/signature/graph/clock reservations still checked by incremental partition; all resulting delays and STA are recomputed.')
