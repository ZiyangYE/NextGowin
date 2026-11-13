"""Bounded ALU-retention beam with across-box native-cell mapping feedback."""
import argparse,copy,json,sys,time,shutil
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from ownpr_image_storage import load_json,sha
from ownpr_heterogeneous_timing import HeterogeneousTiming
from ownpr_cell_timing import NativeCellTiming
from ownpr_extended_timing_model import ExtendedTimingModel
from ownpr_mux_architecture import MuxArchitecture
from ownpr_model_reference import load_reference
from ownpr_carry_simplify import simplify
from ownpr_carry_chains import extract
from native_boundary_cec import prove
from remap_ownpr_logic import boundary,replace
from gw5a_functional_map import blif
from gw5a_arch_mapper import run
from workspace_budget import reserve
RUN_FOLDER=None


def main():
    global RUN_FOLDER
    p=argparse.ArgumentParser(description=__doc__)
    for n in ('source','checkpoint','model','budget','out'):p.add_argument('--'+n,type=Path,required=True)
    p.add_argument('--candidates',type=int,default=3);p.add_argument('--mapped-beam',type=int,default=0,help='Explore actual mapped covers, starting all-soft and adding arbitrary ALU chains; maximum states')
    p.add_argument('--retention-list',type=Path,help='Source-hash-pinned retention states from an earlier mapped search')
    p.add_argument('--hold-aware-pins',action='store_true')
    p.add_argument('--wire-prior',type=Path,help='Experimental native net-mean predictor audit JSON; never replaces routed STA')
    p.add_argument('--passes',type=int,default=2,help='Bounded across-box budget updates, retaining the best cover')
    p.add_argument('--seed-netlist',type=Path,help='Equivalent mapped cover used only to initialize internal timing budgets')
    p.add_argument('--recipe',choices=('lut4','wide_pins','local_wide_pins'),default='wide_pins');a=p.parse_args()
    if not 1<=a.candidates<=4:p.error('Bounded candidate count')
    if a.mapped_beam and not 2<=a.mapped_beam<=32:p.error('Mapped beam must allow2..32 actual states')
    if not 1<=a.passes<=6:p.error('Across-box updates must allow1..6 passes')
    if a.seed_netlist and not a.retention_list:p.error('Warm budget seed needs an explicit source-pinned retention list')
    reserve(ROOT,120000000);budget=load_json(a.budget)
    if time.time()+900>budget['deadline_epoch']:raise TimeoutError('Insufficient complete-attempt time')
    a.out=a.out.resolve();a.out.mkdir(exist_ok=False);RUN_FOLDER=a.out
    snapshot=a.out/'sources';snapshot.mkdir()
    for path in [Path(__file__),*[ROOT/'scripts'/name for name in ('ownpr_heterogeneous_timing.py','ownpr_loaded_wire_prior.py','ownpr_hold_aware_pins.py','gw5a_arch_mapper.py','remap_ownpr_logic.py','ownpr_ff_configuration.py','ownpr_carry_outputs.py')]]:shutil.copyfile(path,snapshot/path.name)
    if a.wire_prior:
        shutil.copyfile(a.wire_prior,snapshot/'loaded_wire_prior_audit.json')
        (snapshot/'loaded_wire_prior_provenance.json').write_text(json.dumps(dict(source=str(a.wire_prior.resolve()),sha256=sha(a.wire_prior)),indent=2))
    row=load_json(a.checkpoint)['designs'][0]
    for path,h in row['selected_source_sha256'].items():
        if sha(ROOT/path)!=h:raise ValueError('Checkpoint changed')
    state={k:ROOT/v for k,v in row['selected'].items()};_,_,model=load_reference(a.model,state['placement'],state['routes'],state['wire'])
    original=load_json(a.source);source,removed=simplify(original);reference=load_json(state['placement']/'packed.json')
    source_proof=prove(original,reference,a.out/'source_boundary_cec',100)
    if original!=reference and (not source_proof or not all(x['cec']['equivalent'] for x in source_proof)):raise ValueError('Source/reference boundary functional identity failed')
    engine=HeterogeneousTiming(reference,model,NativeCellTiming(),ExtendedTimingModel(),MuxArchitecture(),wire_prior=load_json(a.wire_prior)['model'] if a.wire_prior else None)
    chains=extract(source['modules']['top'])['chains'];heads=tuple(sorted(c['cells'][0] for c in chains));cache={};records=[]
    def raw(keep):
        selected,constants,network,info=boundary(source,'selected' if keep else 'all',list(keep) if keep else None)
        return replace(source,selected,constants,network,network),selected,constants,network,info
    def score(keep):
        keep=tuple(sorted(keep))
        if keep not in cache:
            design,*_=raw(keep);_,metrics,info=engine.evaluate(design)
            # Proxy only: true hold/routed costs are mandatory later. Rank
            # all setup endpoint groups, then aggregate hold and cell count.
            key=(-metrics['setup']['wns'],-metrics['setup']['tns'],metrics['hold']['violations'],len(design['modules']['top']['cells']))
            cache[keep]=dict(key=key,setup={k:metrics['setup'][k] for k in ('wns','tns','violations')},hold={k:metrics['hold'][k] for k in ('wns','tns','violations')},adapter_count=len(info['ff_adapter_endpoints']))
        return cache[keep]['key']
    # Retention is chosen from topology/timing, not chain-length thresholds or
    # design names. Every level may remove any chain; keep a small beam.
    beam=[heads];archive={heads,()}
    for _ in range(len(heads)):
        if time.time()+750>budget['deadline_epoch']:break
        offered={tuple(x for x in keep if x!=head) for keep in beam for head in keep}
        if not offered:break
        beam=sorted(offered,key=score)[:3];archive.update(beam)
    ordered=sorted(archive,key=score);chosen=[]
    # Avoid merely repeating all-native/all-soft policies when a joint
    # alternative exists. Both controls remain in the recorded proxy archive.
    mixed=[x for x in ordered if x and x!=heads]
    for keep in [*(mixed[:max(1,a.candidates-1)]),()]:
        if keep not in chosen:chosen.append(keep)
        if len(chosen)>=a.candidates:break
    result=dict(status='mapping',original_budget=budget,recipe=a.recipe,source_sha256={str(x.resolve()):sha(x) for x in (a.source,a.checkpoint,a.model/'model.json',a.model/'manifest.json',Path(__file__),ROOT/'scripts/ownpr_heterogeneous_timing.py')},removed_idempotent_carry_stages=removed,
        chains=[dict(head=c['cells'][0],length=c['length']) for c in chains],proxy_archive=[dict(retained_chain_heads=list(k),**cache[k]) for k in sorted(cache)],candidates=records,source_to_reference_boundary_proof=source_proof,source_snapshots_sha256={str(x.resolve()):sha(x) for x in snapshot.iterdir()},native_timing_verified=False,goal_complete=False)
    def save():(a.out/'manifest.json').write_text(json.dumps(result,indent=2))
    save()
    def actual_score(record):
        row=record['passes'][record.get('selected_iteration',len(record['passes'])-1)]
        return (-row['proxy_setup']['wns'],-row['proxy_setup']['tns'],row['proxy_hold']['violations'],row['cells'])
    def states():
        if a.retention_list:
            selection=load_json(a.retention_list)
            if selection['source_sha256']!=sha(a.source):raise ValueError('Retention list has another source')
            for keep in selection['states']:
                if len(keep)!=len(set(keep)) or not set(keep)<=set(heads):raise ValueError('Unknown/duplicate chain selection')
                yield tuple(sorted(keep))
            return
        if not a.mapped_beam:
            yield from chosen;return
        seen={()};yield ();frontier=[()]
        while len(seen)<a.mapped_beam:
            offered=sorted({tuple(sorted((*keep,head))) for keep in frontier for head in heads if head not in keep}-seen)
            if not offered:return
            level=[]
            for keep in offered:
                if len(seen)>=a.mapped_beam:break
                seen.add(keep);yield keep;level.append(records[-1])
            frontier=[tuple(x['retained_chain_heads']) for x in sorted(level,key=actual_score)[:3]]
    result['retention_search']='actual_mapped_cover_beam' if a.mapped_beam else 'raw_function_proxy_beam';result['maximum_actual_states']=a.mapped_beam or len(chosen);save()
    for i,keep in enumerate(states()):
        if time.time()+650>budget['deadline_epoch']:raise TimeoutError('Original full-attempt deadline reserve')
        folder=a.out/('candidate_'+str(i));folder.mkdir();design,selected,constants,network,info=raw(keep);candidate=dict(index=i,retained_chain_heads=list(keep),**info,passes=[]);records.append(candidate);save()
        if a.seed_netlist:
            seed=load_json(a.seed_netlist);seed_proof=prove(original,seed,folder/'seed_cec',100)
            if not seed_proof or not all(x['cec']['equivalent'] for x in seed_proof):raise ValueError('Warm budget seed is not source-equivalent')
            candidate['warm_budget_seed']=dict(path=str(a.seed_netlist.resolve()),sha256=sha(a.seed_netlist),proof=seed_proof);design=seed
        (folder/'source.blif').write_text(blif(network))
        best=None;best_design=None;best_iteration=None
        for iteration in range(a.passes):
            if time.time()+650>budget['deadline_epoch']:raise TimeoutError('Across-box iteration exhausted original complete-attempt reserve')
            constraints,audit=engine.mapping_constraints(design,network,True);path=folder/('constraints_'+str(iteration)+'.json');path.write_text(json.dumps(constraints,indent=2));(folder/('across_box_'+str(iteration)+'.json')).write_text(json.dumps(audit,indent=2))
            mapped,mn=run(folder/'source.blif',path,ROOT/'results/timing/gowin138_model.json',folder/('mapper_'+str(iteration)),a.recipe)
            design=replace(source,selected,constants,network,mn);_,metrics,cost=engine.evaluate(design)
            candidate['passes'].append(dict(iteration=iteration,proxy_setup={k:metrics['setup'][k] for k in ('wns','tns','violations')},proxy_hold={k:metrics['hold'][k] for k in ('wns','tns','violations')},adapters=len(cost['ff_adapter_endpoints']),optimization_requirement_shift_ns=audit['optimization_only_uniform_requirement_shift_ns'],cells=len(design['modules']['top']['cells'])))
            (folder/('netlist_'+str(iteration)+'.json')).write_text(json.dumps(design,indent=2));save()
            score_now=(-metrics['setup']['wns'],-metrics['setup']['tns'],metrics['hold']['violations'],len(design['modules']['top']['cells']))
            if best is None or score_now<best:best=score_now;best_design=copy.deepcopy(design);best_iteration=iteration
        design=best_design;candidate['selected_iteration']=best_iteration
        if a.hold_aware_pins:
            from ownpr_hold_aware_pins import improve
            design,pin_audit=improve(design,engine);candidate['hold_pin_search']=pin_audit
        # Realize the same calibrated entry/exit resources charged by the
        # across-box proxy before placement; no unroutable COUT side port.
        from ownpr_carry_outputs import materialize_heads,materialize
        design,head_bridges=materialize_heads(design);design,tail_bridges=materialize(design)
        candidate['materialized_head_bridges']=head_bridges;candidate['materialized_tail_bridges']=tail_bridges
        proof=prove(original,design,folder/'cec',100)
        if not proof or not all(x['cec']['equivalent'] for x in proof):raise ValueError('Final source-matched CEC failed')
        (folder/'netlist.json').write_text(json.dumps(design,indent=2));candidate.update(status='mapped_source_cec_passed',proof=proof,netlist_sha256=sha(folder/'netlist.json'));save()
    result.update(status='across_box_candidates_mapped_not_native_verified',within_original_deadline=time.time()<budget['deadline_epoch']);save();print([(r['index'],r['preserved_alu_count'],r['passes'][-1]) for r in records])


if __name__=='__main__':
    try:main()
    except Exception as error:
        if RUN_FOLDER is not None:
            f=RUN_FOLDER/'manifest.json';m=load_json(f) if f.exists() else {};m.update(status='failed',error=str(error),native_timing_verified=False);f.write_text(json.dumps(m,indent=2))
        raise
