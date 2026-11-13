"""Locate exact eight-input cuts that existing seven-input search cannot cover.

Produces cofactor truth certificates and conservative leaf counts only. No
physical candidate, routing gain, complete cut enumeration, or STA is claimed.
"""
import argparse,json,math
from pathlib import Path
from ownpr_image_storage import load_json,sha
from ownpr_multilevel_cut import Cuts
from ownpr_mux_macros import tree
from gw5a_functional_map import cofactor,support
from ownpr_bidir_timing import analyze


def inspect(design,layout,model=None,require_full_support=True):
    graph=Cuts(design,k=7);memo={};visiting=set()
    def leaves(bit):
        if bit in graph.constants:return frozenset()
        if bit in memo:return memo[bit]
        if bit in visiting:raise ValueError('Combinational cycle')
        name=graph.drivers.get(bit)
        if name not in graph.functions:return frozenset([bit])
        visiting.add(bit);result=frozenset().union(*(leaves(b) for b in graph.functions[name][0]));visiting.remove(bit);memo[bit]=result;return result
    metrics=analyze(model['late'],model['setup_requirements']) if model else None
    rows=[]
    for macro in layout.get('wide_macros',[]):
        root=macro['root'];out=graph.functions[root][1];boundary=tuple(sorted(leaves(out)))
        if len(boundary)!=8:continue
        spec=tree(design,root,macro['root_slot'])
        if any(layout['clusters'][n].get('ff') for n in spec['leaves']):continue
        mask=(1<<256)-1;values={b:sum(((a>>i)&1)<<a for a in range(256)) for i,b in enumerate(boundary)};inside=set()
        def evaluate(bit):
            if bit in values:return values[bit]
            if bit in graph.constants:return mask if graph.constants[bit]=='1' else 0
            name=graph.drivers[bit];inputs,_,truth=graph.functions[name];args=[evaluate(b) for b in inputs];word=0
            for assignment in range(1<<len(args)):
                if not (truth>>assignment)&1:continue
                term=mask
                for i,value in enumerate(args):term &= value if (assignment>>i)&1 else mask^value
                word |= term
            inside.add(name);values[bit]=word;return word
        truth=evaluate(out)
        if require_full_support and len(support(truth,8))!=8:continue
        variants=[]
        for select in range(8):
            branches=[cofactor(truth,8,select,v) for v in (0,1)];widths=[len(support(t,7)) for t in branches]
            # Exhaustively reassemble the two independently evaluated cofactors.
            rebuilt=0
            for a in range(256):
                address=(a&((1<<select)-1))|((a>>(select+1))<<select)
                rebuilt|=((branches[(a>>select)&1]>>address)&1)<<a
            if rebuilt!=truth:raise ValueError('Cofactor reconstruction mismatch')
            leaves_count=sum(1 if w<=4 else 1<<(w-4) for w in widths)+1
            variants.append({'select_bit':boundary[select],'cofactor_widths':widths,'cofactor_truth_hex':[format(t,'032x') for t in branches],
                             'lut4_slots_upper_bound':leaves_count,'new_macro_levels':[max(4,w) for w in widths],
                             'reconstruction_checked_assignments':256})
        point=root+'/O';slack=min(metrics['slack'][point]) if metrics and point in metrics['slack'] else None
        rows.append({'root':root,'native_root_level':spec['level'],'cut_bits':list(boundary),'truth_hex':format(truth,'064x'),
                     'upstream_cells':sorted(inside),'old_root_macro_lut4_slots':len(spec['leaves']),
                     'setup_slack_ns':slack if slack is None or math.isfinite(slack) else None,'variants':variants})
    return sorted(rows,key=lambda r:(r['setup_slack_ns'] is None,r['setup_slack_ns'] or 0,r['root']))


def main():
    p=argparse.ArgumentParser();p.add_argument('--packet',type=Path,required=True);p.add_argument('--out',type=Path,required=True);a=p.parse_args();rows=[]
    for case in load_json(a.packet)['designs']:
        placement=Path(case['selected']['placement']);model_paths=[Path(f) for f in case.get('selected_source_sha256',{}) if Path(f).name=='model.json']
        model=load_json(model_paths[0]) if len(model_paths)==1 else None
        candidates=inspect(load_json(placement/'packed.json'),load_json(placement/'placement.json'),model)
        row={'design':case['design'],'source_netlist_sha256':sha(placement/'packed.json'),'source_layout_sha256':sha(placement/'placement.json'),
             'model_path':str(model_paths[0]) if len(model_paths)==1 else None,'candidate_count':len(candidates),'candidates':candidates};rows.append(row)
        print(case['design'],len(candidates),[(r['root'],r['setup_slack_ns']) for r in candidates[:4]],flush=True)
    with a.out.open('x') as f:json.dump({'scope':__doc__,'packet_sha256':sha(a.packet),'designs':rows},f,indent=2);f.write('\n')


if __name__=='__main__':main()
