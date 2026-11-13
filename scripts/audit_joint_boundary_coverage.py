"""Inventory complete timing boundaries and regions; never infer missing coverage.

This is optimization feedback, not native timing signoff or proof of movable
register/clock/hard-resource support. Constants and mode-inactive inputs are
reported separately as unresolved until a mode-aware classifier proves them.
"""
import argparse,json,math
from collections import defaultdict
from pathlib import Path
from ownpr_bidir_timing import analyze
from ownpr_critical_chain_move import locations
from ownpr_image_storage import load_json,sha
from ownpr_device import ROOT
from workspace_budget import reserve


def role(name,pin,cells,layout):
    cell=cells.get(name,{});kind=cell.get('type','unknown');p=pin.upper()
    family=layout.get('hard_clusters',{}).get(name,{}).get('family')
    if kind.startswith('DFF'):
        if p in ('CLK','CK'):return 'register_clock'
        if p in ('CE','EN'):return 'register_enable'
        if p in ('RESET','SET','CLEAR','PRESET','R','S'):return 'register_reset_set'
        return 'register_data' if p=='D' else 'register_other'
    if family in ('bsram','dsp'):
        if 'CLK' in p:return family+'_clock'
        if p.startswith(('AD','ADDR')):return family+'_address'
        if p.startswith(('DI','DO','DATA')):return family+'_data'
        return family+'_control_or_operand'
    if 'BUF' in kind and kind in ('OBUF','TBUF','IOBUF'):return 'io_output_or_control'
    return 'other_'+kind


def summarize(model,design,layout,region_size=16):
    if region_size<=0:raise ValueError('Region size must be positive')
    cells=design['modules']['top']['cells'];points=locations(layout)
    groups=defaultdict(lambda:dict(endpoints=0,violations=0,tns_ns=0.,wns_ns=math.inf))
    endpoint_rows=[];inventory=[]
    for mode,key,requirements in (('setup','late','setup_requirements'),('hold','early','hold_requirements')):
        graph=model[key];result=analyze(graph,model[requirements],mode=='hold');nodes=set(graph['nodes'])
        for endpoint,slack in sorted(result['endpoints'].items()):
            name,_,pin=endpoint.rpartition('/');point=points.get(name)
            region=[v//region_size for v in point] if point else None
            category=role(name,pin,cells,layout);group=(mode,category,tuple(region) if region else None)
            row=groups[group];row['endpoints']+=1;row['violations']+=int(slack<0)
            row['tns_ns']+=min(0.,slack);row['wns_ns']=min(row['wns_ns'],slack)
            endpoint_rows.append(dict(mode=mode,endpoint=endpoint,role=category,region=region,slack_ns=slack))
        # Include every physical state/hard/IO input, including those absent
        # from terminal requirements. Presence as a vertex is not a check.
        for name,cell in sorted(cells.items()):
            boundary=(cell['type'].startswith('DFF') or name in layout.get('hard_clusters',{}) or cell['type'] in ('OBUF','TBUF','IOBUF'))
            if not boundary:continue
            for pin,direction in sorted(cell.get('port_directions',{}).items()):
                if direction not in ('input','inout'):continue
                endpoint=name+'/'+pin;category=role(name,pin,cells,layout)
                inventory.append(dict(mode=mode,endpoint=endpoint,role=category,primitive=cell['type'],
                    present_in_graph=endpoint in nodes,has_terminal_check=endpoint in model[requirements],
                    classification='clock_requires_separate_audit' if category.endswith('_clock') else 'checked' if endpoint in model[requirements] else 'unresolved_constant_inactive_or_missing_check'))
    rows=[dict(mode=m,role=k,region=list(r) if r else None,**v) for (m,k,r),v in sorted(groups.items(),key=lambda x:str(x[0]))]
    counts=defaultdict(int)
    for row in inventory:counts[row['mode']+'/'+row['classification']]+=1
    return dict(status='complete_model_boundary_inventory',timing_signoff=False,
        movable_state_or_clock_support_proven=False,region_size_tiles=region_size,
        groups=rows,endpoints=endpoint_rows,physical_input_inventory=inventory,
        input_classification_counts=dict(counts),
        scope='All supplied model endpoints and physical state/hard/IO inputs; absent checks are unresolved, not assumed safe or inactive.')


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for n in ('model','placement','out'):p.add_argument('--'+n,type=Path,required=True)
    p.add_argument('--region-size',type=int,default=16);a=p.parse_args()
    reserve(ROOT,8_000_000)
    files=[a.model/'model.json',a.placement/'packed.json',a.placement/'placement.json',Path(__file__)]
    result=summarize(*(load_json(f) for f in files[:3]),region_size=a.region_size)
    result['source_sha256']={str(f.resolve()):sha(f) for f in files}
    with a.out.open('x') as stream:json.dump(result,stream,indent=2,allow_nan=False)
    print(json.dumps(dict(groups=len(result['groups']),endpoints=len(result['endpoints']),input_classification_counts=result['input_classification_counts'])))


if __name__=='__main__':main()
