"""Prepare and prove a generic multi-CFU cut candidate from a checked packet."""
import argparse,json,time,shutil
from pathlib import Path
from ownpr_image_storage import load_json,sha
from workspace_budget import reserve
from ownpr_device import ROOT,Device
from native_boundary_cec import prove


def main():
    p=argparse.ArgumentParser();p.add_argument('--packet',type=Path,required=True);p.add_argument('--case',required=True)
    p.add_argument('--out',type=Path,required=True);p.add_argument('--count',type=int,default=2);p.add_argument('--select-rank',type=int,default=0)
    p.add_argument('--output-weight',type=float,default=8.)
    p.add_argument('--method',choices=('shannon','acd','acd-joint'),default='shannon')
    p.add_argument('--pin-policy',choices=('arrival','load'),default='arrival')
    p.add_argument('--frontier',choices=('sink','exit'),default='sink');a=p.parse_args()
    if a.method=='shannon' and a.pin_policy!='arrival':p.error('Load ordering is implemented only for ACD')
    if a.method.startswith('acd'):from ownpr_shared_encoder import select,apply
    else:from ownpr_large_cut import select,apply
    if not 1<=a.count<=(16 if a.method.startswith('acd') else 8) or not 0<=a.select_rank<8 or not 1<=a.output_weight<=32:p.error('Bounded proposal controls required')
    reserve(ROOT,30_000_000);a.out.mkdir(parents=True,exist_ok=False);start=time.time()
    record={'status':'preparing','timing_signoff':False,'vendor_pnr_executed':False,'open_chip_database_used':False}
    def save():record['wall_seconds']=time.time()-start;(a.out/'manifest.json').write_text(json.dumps(record,indent=2)+'\n')
    sources=['ownpr_large_cut.py','prepare_large_cut.py','analyze_eight_input_macros.py','ownpr_global_timing_frontier.py']
    if a.method.startswith('acd'):sources+=['ownpr_shared_encoder.py','ownpr_multioutput_acd.py','analyze_large_cut_acd.py','acd_reference.py']
    record['source_code_sha256']={name:sha(ROOT/'scripts'/name) for name in sources}
    for name in sources:shutil.copyfile(ROOT/'scripts'/name,a.out/(name+'.txt'))
    save()
    try:
        matches=[r for r in load_json(a.packet)['designs'] if r['design']==a.case]
        if len(matches)!=1:raise ValueError('Case is missing/ambiguous in source packet')
        source=matches[0]
        for f,h in source['selected_source_sha256'].items():
            if sha(Path(f))!=h:raise ValueError('Source evidence changed: '+f)
        base=Path(source['selected']['placement']);models=[Path(f) for f in source['selected_source_sha256'] if Path(f).name=='model.json']
        if len(models)!=1:raise ValueError('Need one source-bound full timing model')
        before=load_json(base/'packed.json');layout=load_json(base/'placement.json');model=load_json(models[0])
        if a.method.startswith('acd'):rows,metrics,frontier=select(before,layout,model,a.count,frontier_policy=a.frontier)
        else:rows,metrics,frontier=select(before,layout,model,a.count)
        (a.out/'frontier.json').write_text(json.dumps(frontier,indent=2)+'\n')
        extra={'joint':True} if a.method=='acd-joint' else {}
        if a.method.startswith('acd'):extra['pin_policy']=a.pin_policy
        design,placed,change=apply(before,layout,Device(),rows,metrics,a.select_rank,a.output_weight,**extra)
        (a.out/'change.json').write_text(json.dumps(change,indent=2)+'\n')
        record['proof']=prove(before,design,a.out/'cec',120)
        from ownpr_mux_macros import validate as wide_validate
        from ownpr_carry_macros import validate as carry_validate
        from ownpr_ff_configuration import compile_initial
        from ownpr_hard_architecture import HardArchitecture
        device=Device();record['wide_validation']=wide_validate(design,placed,device);record['carry_validation']=carry_validate(design,placed,device)
        compile_initial(design,placed)
        if placed.get('hard_clusters'):record['hard_validation']=HardArchitecture().validate(design,placed,device)
        for n,row in layout['clusters'].items():
            if row.get('ff') and placed['clusters'].get(n)!=row:raise ValueError('Moved state')
        (a.out/'packed.json').write_text(json.dumps(design));(a.out/'placement.json').write_text(json.dumps(placed,indent=2))
        if any(sha(ROOT/'scripts'/name)!=h for name,h in record['source_code_sha256'].items()):raise ValueError('Implementation changed while preparing candidate')
        record.update(status='placed',source_packet_sha256=sha(a.packet),source_case=a.case,source_placement=str(base),source_constraints=source['constraints'],
            source_netlist_sha256=sha(base/'packed.json'),source_layout_sha256=sha(base/'placement.json'),source_model_sha256=sha(models[0]),
            netlist_sha256=sha(a.out/'packed.json'),placement_sha256=sha(a.out/'placement.json'),lut_delta=change['lut_delta'],register_delta=0,
            clock_endpoints_unchanged=True,state_and_hard_resources_unchanged=True,interface_cycles_changed=False,
            selected_roots=[r['root'] for r in rows],physical_lut8_used=False,method=a.method,pin_policy=a.pin_policy,frontier_policy=a.frontier,
            source_implementation_sha256=sha(ROOT/'scripts'/('ownpr_shared_encoder.py' if a.method.startswith('acd') else 'ownpr_large_cut.py')))
        save();print(json.dumps({k:record[k] for k in ('status','selected_roots','lut_delta','wall_seconds')}))
    except Exception as error:record.update(status='failed',error=repr(error));save();raise


if __name__=='__main__':main()
