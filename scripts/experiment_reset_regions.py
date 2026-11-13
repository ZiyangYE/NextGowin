"""Single-variable reset fanout partition experiment; no register replication."""
import argparse,json,time,subprocess,sys,shutil
from pathlib import Path
from ownpr_device import ROOT,Device
from ownpr_hold_buffers import receiver_site,insert_buffers
from ownpr_image_storage import load_json,sha,read_text
from ownpr_input_partition import select_input
from native_boundary_cec import prove
from ownpr_ff_configuration import compile_initial
from ownpr_mux_macros import validate
from workspace_budget import reserve
from run_ownpr_pin_closure import stop_owned_process_tree

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--out',type=Path,required=True);parser.add_argument('--buffer-fraction',type=float,default=1.)
    parser.add_argument('--report',type=Path,default=ROOT/'results/vexriscv_shared_global_001/reconciled_001/qualified_replay_checkpoint.json')
    parser.add_argument('--case',default='vexriscv');parser.add_argument('--groups',type=int,choices=(2,4,8),default=4)
    options=parser.parse_args()
    if not 0<=options.buffer_fraction<=1:parser.error('buffer fraction must be between source (0) and region (1)')
    start=time.monotonic();reserve(ROOT,1_500_000_000)
    out=options.out.resolve();out.mkdir(exist_ok=False);shutil.copyfile(__file__,out/'experiment_source.py.txt')
    row=next(r for r in load_json(options.report)['designs'] if r['design']==options.case)
    for path,digest in row['selected_source_sha256'].items():
        if sha(ROOT/path)!=digest:raise ValueError('Changed baseline evidence: '+path)
    state={k:ROOT/v for k,v in row['selected'].items()};base=state['placement']
    old=load_json(base/'packed.json');placed=load_json(base/'placement.json');cells=old['modules']['top']['cells']
    names=load_json(state['sta']/'name_map.json')['instances']
    chosen,ranking=select_input(old,placed,names,read_text(state['sta']/'probe.tr'),options.groups)
    source=chosen['source'];sinks=chosen['sinks']
    # Deterministic balanced spatial bisection, one buffer per region.
    groups=[sinks]
    for _ in range(options.groups.bit_length()-1):
        split=[]
        for group in groups:
            axis=max((2,3),key=lambda i:max(s[i] for s in group)-min(s[i] for s in group))
            ordered=sorted(group,key=lambda s:(s[axis],s[0],s[1]));mid=len(group)//2;split.extend([ordered[:mid],ordered[mid:]])
        groups=split
    representatives=[]
    for group in groups:
        r=sum(s[2] for s in group)/len(group);c=sum(s[3] for s in group)/len(group)
        representatives.append(min(group,key=lambda s:abs(s[2]-r)+abs(s[3]-c)))
    device=Device();cal=load_json(ROOT/'results/native_device_lut_004/lut_encoding_audit.json')
    assert cal['source_fse_sha256']==device.fse.sha256 and all(r['match'] for r in cal['checks'])
    targets=[dict(receiver=s[0],pin=s[1],source=source) for s in representatives]
    new,updated,added=insert_buffers(old,placed,device,cal,targets,1)
    for group,entry in zip(groups,added):
        output=new['modules']['top']['cells'][entry['instance']]['connections']['F']
        for n,pin,_,_ in group:new['modules']['top']['cells'][n]['connections'][pin]=output.copy()
    if options.buffer_fraction!=1:
        source_row,source_col,_=placed['fixed_io'][source]['native_ports']['out'];source_row-=1;source_col-=1
        occupied={(v['row'],v['column'],v['slot']) for v in placed['clusters'].values()}
        excluded={(v['row'],v['column']) for kind in ('arithmetic_clusters','mux_clusters') for v in placed.get(kind,{}).values()}
        for v in placed.get('hard_clusters',{}).values():excluded.update((v['row'],v['column']+i) for i in range(3))
        for entry in added:
            r,c,_=entry['site'];fraction=options.buffer_fraction
            target=(source_row+fraction*(r-source_row),source_col+fraction*(c-source_col))
            sites=((abs(y-target[0])+abs(x-target[1]),y,x,s) for y in range(device.rows) for x in range(device.cols) if device.grid[y][x] in cal['tile_types'] and (y,x) not in excluded for s in range(8) if (y,x,s) not in occupied)
            _,y,x,s=min(sites);occupied.add((y,x,s));updated['clusters'][entry['instance']].update(row=y,column=x,slot=s);entry['site']=[y,x,s]
    assert updated['fixed_io']==placed['fixed_io']
    validate(new,updated,device);compile_initial(new,updated)
    candidate=out/'placement';candidate.mkdir();proof=prove(old,new,candidate/'cec',120)
    for n,data in [('packed.json',new),('placement.json',updated)]: (candidate/n).write_text(json.dumps(data))
    manifest=dict(status='placed',buffer_fraction=options.buffer_fraction,netlist_sha256=sha(candidate/'packed.json'),placement_sha256=sha(candidate/'placement.json'),proof=proof,source=source,groups=groups,added=added,clock_endpoints_unchanged=True,fixed_io_unchanged=True,pipeline_stages_added=0)
    (candidate/'manifest.json').write_text(json.dumps(manifest,indent=2))
    seconds=3500-int(time.monotonic()-start)
    contract={k:ROOT/v for k,v in row['constraints'].items()}
    args=[sys.executable,str(ROOT/'scripts/run_ownpr_checkpoint.py'),'--placement',str(candidate),'--cst',str(contract['cst']),'--sdc',str(contract['sdc']),'--io-reference',str(contract['io_reference']),'--reuse-routes',str(state['routes']),'--clock-tree',str(state['routes']/'clock_tree.json'),'--clock-capture',str(state['clock_capture']),'--search-bias','30','--root-arrival-weight','1','--guide-edge-delay','.05','--guided-corridor','--seconds',str(seconds),'--route-seconds','1800','--out',str(out/'implementation')]
    proc=subprocess.Popen(args,cwd=ROOT,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    try:
        code=proc.wait(timeout=max(1,3500-(time.monotonic()-start)))
        if code:raise RuntimeError('Physical implementation failed: '+str(code))
    except (subprocess.TimeoutExpired,RuntimeError) as error:
        if proc.poll() is None:stop_owned_process_tree(proc)
        (out/'manifest.json').write_text(json.dumps(dict(status='timeout' if isinstance(error,subprocess.TimeoutExpired) else 'failed',error=str(error),experiment_wall_seconds=time.monotonic()-start,accepted=False),indent=2))
        raise
    result=load_json(out/'implementation/manifest.json');result['experiment_wall_seconds']=time.monotonic()-start
    metrics=result['metrics'];baseline=row['metrics']
    result['comparison']=dict(baseline_report=str(options.report),baseline_sdc=str(contract['sdc']),setup_gain_ns=metrics['setup_wns_ns']-baseline['setup_wns_ns'],hold_gain_ns=metrics['hold_wns_ns']-baseline['hold_wns_ns'])
    result['accepted']=bool(result['experiment_wall_seconds']<=3500 and metrics['native_constraints_satisfied'] and metrics['other_checks_passed'] and metrics['setup_violations']==metrics['hold_violations']==0 and result['comparison']['setup_gain_ns']>0)
    result['higher_frequency_qualified']=False
    (out/'manifest.json').write_text(json.dumps(result,indent=2));print(json.dumps(result['metrics']))
if __name__=='__main__':main()
