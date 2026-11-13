"""Compare physical packing capacity; historical references are not speed proofs."""
import argparse,json
from collections import Counter,defaultdict
from pathlib import Path
from ownpr_image_storage import load_json,sha
from workspace_budget import reserve

ROOT=Path(__file__).resolve().parents[1]


def statistics(sites):
    return dict(occupied_cfus=len(sites),resources=dict(sum(sites.values(),Counter())),
        register_cfus=sum(v['ff']>0 for v in sites.values()),
        ff_per_cfu_histogram=dict(sorted(Counter(v['ff'] for v in sites.values()).items())),
        wide_cfus=sum(v['mux']>0 for v in sites.values()),
        wide_with_ff_cfus=sum(v['mux']>0 and v['ff']>0 for v in sites.values()),
        carry_with_ff_cfus=sum(v['alu']>0 and v['ff']>0 for v in sites.values()),
        at_least_seven_lut_cfus=sum(v['lut']>=7 for v in sites.values()))


def own(folder):
    layout=load_json(folder/'placement.json');design=load_json(folder/'packed.json');cells=design['modules']['top']['cells'];sites=defaultdict(Counter)
    for row in layout['clusters'].values():
        s=sites[row['row'],row['column']];s['lut']+=1
        if row.get('ff'):s['ff']+=1
    for key,kind in (('arithmetic_clusters','alu'),('mux_clusters','mux')):
        for row in layout[key].values():sites[row['row'],row['column']][kind]+=1
    controls=Counter();constant_values={'0':0,'1':1}
    for c in cells.values():
        if c['type'] in ('GND','VCC'):constant_values[next(iter(c['connections'].values()))[0]]=int(c['type']=='VCC')
    for row in layout['clusters'].values():
        if not row.get('ff') or row.get('wide_ff_root'):continue
        c=cells[row['ff']];typ=c['type']
        if typ not in ('DFFRE','DFFSE','DFFCE','DFFPE'):raise ValueError('Unsupported physical register')
        pins=tuple((p,tuple(bits)) for p,bits in sorted(c['connections'].items()) if p not in ('D','Q'))
        controls[typ,tuple(sorted(c.get('parameters',{}).items())),pins]+=1
    groups=[]
    for (typ,params,pins),count in controls.most_common():
        groups.append(dict(cell_type=typ,parameters=dict(params),controls=dict(pins),count=count,
            constants={p:[constant_values.get(b) for b in bs] for p,bs in pins}))
    result=statistics(sites);result.update(folder=str(folder),sources={p:sha(folder/p) for p in ('placement.json','packed.json')},
        ordinary_register_control_signature_count=len(controls),control_groups=groups,
        theoretical_compatible_pairs=sum(v//2 for v in controls.values()),
        pair_bound_scope='Logical signature count only. Ignores location, timing, available LUT slots and configuration admission; not a legal packing or timing gain.')
    return result


def reference(folder):
    rows=load_json(folder/'primitive_bindings.json');meta=load_json(folder/'manifest.json')
    if meta['status']!='captured':raise ValueError('Reference capture incomplete')
    kinds={'.?AVReg@NETLIST@@':'ff','.?AVLut4@NETLIST@@':'lut','.?AVMux2@NETLIST@@':'mux','.?AVAlu@NETLIST@@':'alu'};sites=defaultdict(Counter)
    for row in rows:
        kind=kinds[row['logical_class']];header=row['physical_header'];sites[tuple(header[1:3])][kind]+=1
    result=statistics(sites);result.update(folder=str(folder),sources={p:sha(folder/p) for p in ('primitive_bindings.json','manifest.json')},
        vendor_pnr_reference_only=True,same_current_netlist=False,
        scope='Historical reference, different mapped/optimized netlist. Capacity evidence only; neither a fair frequency ratio nor proof all combinations are supported.')
    return result


def main():
    p=argparse.ArgumentParser();p.add_argument('--placement',type=Path,required=True);p.add_argument('--reference',type=Path,action='append',required=True);p.add_argument('--out',type=Path,required=True);a=p.parse_args()
    if a.out.exists():raise FileExistsError(a.out)
    result=dict(status='packing_inventory_compared',own=own(a.placement),references=[reference(f) for f in a.reference],
        data_only_no_pnr_executed=True,frequency_improvement_proven=False)
    reserve(ROOT,1_000_000);a.out.parent.mkdir(parents=True,exist_ok=True);a.out.write_text(json.dumps(result,indent=2))
    print(json.dumps({k:v for k,v in result['own'].items() if k not in ('control_groups','sources')},indent=2))


if __name__=='__main__':main()
