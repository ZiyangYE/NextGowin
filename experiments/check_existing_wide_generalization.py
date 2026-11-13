"""Source-matched cross-design structural/CEC guard, not a performance test."""
import argparse,json,shutil,sys,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from ownpr_image_storage import load_json,sha
from ownpr_existing_wide_remap import info,apply
from ownpr_mux_macros import validate
from ownpr_device import Device
from native_boundary_cec import prove
from workspace_budget import reserve


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--checkpoint',type=Path,action='append',required=True)
    p.add_argument('--family',choices=('canonical','general','cross'),default='canonical')
    p.add_argument('--out',type=Path,required=True);p.add_argument('--budget',type=Path,required=True);a=p.parse_args()
    reserve(ROOT,40_000_000);a.out=a.out.resolve();a.out.mkdir(exist_ok=False);budget=load_json(a.budget)
    cases={};sources={}
    for file in a.checkpoint:
        for row in load_json(file)['designs']:cases[row['design']]=row
        sources[str(file)]=sha(file)
    result=dict(status='running',family=a.family,performance_generalization_proved=False,vendor_pnr_executed=False,original_budget=budget,checkpoints=sources,cases=[])
    def save():(a.out/'manifest.json').write_text(json.dumps(result,indent=2))
    save();device=Device()
    for name,row in cases.items():
        if time.time()>budget['deadline_epoch']-150:raise TimeoutError('Original trial deadline')
        folder=a.out/name;folder.mkdir();record=dict(design=name,status='checking');result['cases'].append(record);save()
        placement=ROOT/row['selected']['placement'];design=load_json(placement/'packed.json');layout=load_json(placement/'placement.json')
        for name,h in row['selected_source_sha256'].items():
            if sha(ROOT/name)!=h:raise ValueError('Checkpoint source changed')
        changed=design;selected=[];rejected=[]
        for macro in layout.get('wide_macros',[]):
            root=macro['root']
            try:
                if a.family=='canonical':
                    spec,order,_=info(changed,layout,root);changed,_,change=apply(changed,layout,root,[(order[0],spec['level']-1)])
                else:
                    from ownpr_general_wide_recompile import info as general_info,apply as general_apply
                    from ownpr_existing_wide_remap import macro_truth
                    spec,order,members=general_info(changed,layout,root)
                    if a.family=='general':
                        cut=macro_truth(changed,root,order,members);boundary=None
                    else:
                        from ownpr_cross_macro_recompile import expanded_boundaries
                        from ownpr_multilevel_cut import Cuts
                        model=Cuts(changed,k=7,depth=8,limit=48);cut=None
                        for boundary in expanded_boundaries(changed,root,model,order):
                            proposal=model.function(root,boundary)
                            if proposal['cut_width'] and set(proposal['interior'])-members:
                                cut=proposal;break
                        if cut is None:raise ValueError('No cross-macro cut')
                    priority=cut['cut_bits'];last_error=None
                    for bit in priority:
                        try:candidate,_,change=general_apply(changed,layout,root,bit,priority,boundary_override=boundary);break
                        except ValueError as error:last_error=error
                    else:raise ValueError('No fitting nonidentity decomposition: '+str(last_error))
                    changed=candidate
                selected.append(change)
            except ValueError as error:rejected.append(dict(root=root,error=str(error)))
            if len(selected)>=3:break
        record.update(source_packed_sha256=sha(placement/'packed.json'),source_layout_sha256=sha(placement/'placement.json'),transformations=selected,rejections=rejected)
        if selected:
            audit=validate(changed,layout,device);proof=prove(design,changed,folder/'cec',120)
            if not proof or not all(r['cec']['equivalent'] for r in proof):raise ValueError('Cross-design CEC failed')
            (folder/'candidate.json').write_text(json.dumps(changed));record.update(status='structural_and_fixed_state_cec_passed',macro_audit=audit,cec=proof,candidate_sha256=sha(folder/'candidate.json'))
        else:record['status']='no_matching_macro_unchanged'
        record.update(placement_unchanged=True,interface_cycles_changed=False,full_native_sta_performed=False);save()
    result.update(status='structural_generalization_checked_not_performance',within_original_deadline=time.time()<budget['deadline_epoch']);save()
    shutil.copyfile(__file__,a.out/'runner_source.py.txt');snapshots=a.out/'sources';snapshots.mkdir()
    for name in ('ownpr_general_wide_recompile.py','ownpr_cross_macro_recompile.py','ownpr_existing_wide_remap.py','ownpr_multilevel_cut.py','register_control_map.py','native_boundary_cec.py','ownpr_cell_timing.py'):
        shutil.copyfile(ROOT/'scripts'/name,snapshots/name)
    result['implementation_sha256']={str(f.resolve()):sha(f) for f in [a.out/'runner_source.py.txt',*snapshots.iterdir()]};save()
    print(json.dumps(dict(status=result['status'],cases=[(r['design'],r['status'],len(r['transformations'])) for r in result['cases']])))


if __name__=='__main__':main()
