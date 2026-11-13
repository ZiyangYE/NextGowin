"""Jointly cover competing setup paths with disjoint ordinary and wide cuts.

STA selects the window and candidate edges. A bounded LUT budget and exact
CEC precede incremental own P&R; structural coverage is not timing closure.
"""
import argparse,json,shutil,sys,time
from pathlib import Path
from ownpr_device import ROOT,Device
from ownpr_image_storage import load_json,sha
from ownpr_critical_tree_rebuild import verify_feedback
from ownpr_critical_chain_move import apply as validate_placement
from ownpr_timing_cut_remap import candidates as ordinary_candidates,compose
from ownpr_timing_wide_remap import candidates as wide_candidates,transform,place
from native_boundary_cec import prove
from workspace_budget import reserve


def select(options,traces,count=4,window=.5,max_growth=12):
    if not 1<=count<=8 or not 0<window<=5 or not 0<=max_growth<=24:raise ValueError('Invalid batch search limits')
    if not traces:raise ValueError('No timing path witnesses')
    worst=min(p['slack_ns'] for p in traces);pending={i for i,p in enumerate(traces) if p['slack_ns']<=worst+window}
    initial=set(pending);used=set();chosen=[];growth=0
    while pending and len(chosen)<count:
        eligible=[]
        for option in options:
            if {option['parent'],option['root']}&used or growth+option['lut_growth']>max_growth:continue
            covered=set(option['witness_paths'])&pending
            if not covered:continue
            score=(min(traces[i]['slack_ns'] for i in covered),-len(covered),-option['reported_wire_ns'],option['lut_growth'],option['root'],option['kind'])
            eligible.append((score,option,covered))
        if not eligible:break
        _,option,covered=min(eligible,key=lambda row:row[0]);chosen.append(dict(option,newly_covered_paths=sorted(covered)))
        pending-=covered;used.update((option['parent'],option['root']));growth+=option['lut_growth']
    return chosen,dict(requested_changes=count,selected_changes=len(chosen),window_ns=window,lut_growth=growth,lut_growth_limit=max_growth,
        target_paths=sorted(initial),covered_paths=sorted(initial-pending),uncovered_paths=sorted(pending),scope='Reported path structural coverage only; new critical paths and physical costs require full STA.')


def plan(design,layout,ranking,count=4,window=.5,max_growth=12):
    ordinary,orej=ordinary_candidates(design,layout,ranking);wide,wrej=wide_candidates(design,layout,ranking)
    options=[dict(o,parent=o['parent_preserved'],root=o['replaced_root'],kind='ordinary') for o in ordinary]+[dict(o,kind='wide') for o in wide]
    for o in options:
        label=o['root']+'/'+o['old_receiver_pin'];bit=design['modules']['top']['cells'][o['parent']]['connections']['F'][0]
        o['witness_paths']=[i for i,p in enumerate(ranking['traces']) if any(a['endpoint']==label and a['net']=='net_'+str(bit) for a in p['ordinary_wires'])]
        if not o['witness_paths']:raise ValueError('Cut has no matching physical path witness')
    chosen,coverage=select(options,ranking['traces'],count,window,max_growth)
    return chosen,coverage,dict(options=options,rejected_ordinary=orej,rejected_wide=wrej)


def apply_batch(design,layout,device,chosen):
    after=design;placed=layout;changes=[];touched=set()
    for row in chosen:
        if {row['parent'],row['root']}&touched:raise ValueError('Overlapping batch cuts')
        touched.update((row['parent'],row['root']))
        if row['kind'] not in ('ordinary','wide'):raise ValueError('Unsupported batch cut kind')
    for row in chosen:
        if row['kind']=='ordinary':after,change=compose(after,row['parent'],row['root']);physical=dict(placement_unchanged=True)
        elif row['kind']=='wide':
            after,change=transform(after,row['parent'],row['root'],row['late_bit']);placed,physical=place(after,placed,row['root'],device)
        else:raise ValueError('Unsupported batch cut kind')
        changes.append(dict(candidate=row,transform=change,physical=physical))
    validate_placement(after,placed,device,dict(sites={}))
    return after,placed,changes


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for n in ('base','routes','sta','out'):p.add_argument('--'+n,type=Path,required=True)
    p.add_argument('--count',type=int,default=4);p.add_argument('--window',type=float,default=.5);p.add_argument('--lut-growth',type=int,default=12)
    p.add_argument('--prune-unused',action='store_true',help='Prove LUT-input truth independence and remove irrelevant loads before routing; full STA remains mandatory');a=p.parse_args()
    reserve(ROOT,60_000_000);started=time.monotonic();base,routes,sta,out=(v.resolve() for v in (a.base,a.routes,a.sta,a.out))
    verify_feedback(base,routes,sta);design=load_json(base/'packed.json');layout=load_json(base/'placement.json')
    sys.path.insert(0,str(ROOT/'experiments'));from analyze_ownpr_critical_wires import analyze
    ranking=analyze(sta,base,routes,128);chosen,coverage,options=plan(design,layout,ranking,a.count,a.window,a.lut_growth)
    if not chosen:raise ValueError('No admissible joint timing cuts')
    device=Device();after,placed,changes=apply_batch(design,layout,device,chosen);pruned=[]
    if a.prune_unused:
        from prune_ownpr_lut_inputs import prune
        after,pruned=prune(after);validate_placement(after,placed,device,dict(sites={}))
    out.mkdir(exist_ok=False);proof=prove(design,after,out/'cec',120)
    if not proof or not all(r['cec']['equivalent'] for r in proof):raise ValueError('Batch timing cuts failed CEC')
    for name,value in [('packed.json',after),('placement.json',placed),('candidates.json',options),('ranking.json',ranking)]:(out/name).write_text(json.dumps(value,indent=2))
    sources=['ownpr_batch_timing_remap.py','ownpr_timing_cut_remap.py','ownpr_timing_wide_remap.py','ownpr_wide_cone.py','reorder_ownpr_wide_inputs.py','prune_ownpr_lut_inputs.py']
    for name in sources:shutil.copyfile(ROOT/'scripts'/name,out/(name+'.txt'))
    result=dict(status='placed',netlist_sha256=sha(out/'packed.json'),placement_sha256=sha(out/'placement.json'),source_netlist_sha256=sha(base/'packed.json'),source_placement_sha256=sha(base/'placement.json'),source_routes_sha256=sha(routes/'routes.json'),source_sta_sha256=sha(sta/'probe.tr'),
        changes=changes,coverage=coverage,proof=proof,unused_input_pruning=pruned,prune_unused_requested=a.prune_unused,producer_source_sha256={name:sha(out/(name+'.txt')) for name in sources},synthesis_pnr_cooptimization=True,
        state_and_hard_resources_unchanged=True,clock_endpoints_unchanged=True,interface_cycles_changed=False,vendor_pnr_executed=False,physical_timing_verified=False,timing_signoff=False,wall_seconds=time.monotonic()-started)
    (out/'manifest.json').write_text(json.dumps(result,indent=2));print(json.dumps(dict(coverage=coverage,changes=[dict(parent=r['parent'],root=r['root'],kind=r['kind'],wire_ns=r['reported_wire_ns']) for r in chosen]),indent=2))


if __name__=='__main__':main()
