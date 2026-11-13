"""Bounded equivalent-LUT fixture exercising real routed low/high constants."""
import argparse,copy,json,shutil,subprocess,sys,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from ownpr_place import sha
from native_boundary_cec import prove
from run_ownpr_pin_closure import metric,stop_owned_process_tree
from audit_ownpr_native_export import audit
from workspace_budget import reserve


def fixture(design,placed):
    after=copy.deepcopy(design);cells=after['modules']['top']['cells'];constants={}
    for c in cells.values():
        if c['type'] in ('VCC','GND'):constants[int(c['type']=='VCC')]=next(iter(c['connections'].values()))[0]
    if set(constants)!={0,1}:raise ValueError('Fixture needs existing low/high logical constants')
    chosen=[];sites=set()
    for name,c in sorted(cells.items()):
        if c['type']!='LUT1' or int(c['parameters']['INIT'],2)!=2 or name not in placed['clusters']:continue
        place=placed['clusters'][name];site=(place['row'],place['column'])
        if site in sites:continue
        value=len(chosen)%2;c['type']='LUT2';c['parameters']['INIT']='1000' if value else '0010'
        c['connections']['I1']=[constants[value]];c['port_directions']['I1']='input';sites.add(site)
        chosen.append(dict(instance=name,pin='I1',value=value,bit=constants[value],site=list(site),
            original_truth=2,new_truth=int(c['parameters']['INIT'],2),input_functionally_relevant=True))
        if len(chosen)==4:break
    if len(chosen)!=4:raise ValueError('Fixture needs four buffered LUTs in distinct CFUs')
    return after,chosen


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('state','cst','sdc','io-reference','out'):p.add_argument('--'+name,type=Path,required=True)
    p.add_argument('--seconds',type=int,default=480);a=p.parse_args()
    if not 1<=a.seconds<=3600:p.error('Require a positive budget of at most one hour')
    start=time.monotonic();deadline=start+a.seconds;storage=reserve(ROOT,500_000_000)
    source=json.loads(a.state.read_text())['selected'];base=Path(source['placement']);sta=Path(source['sta'])
    audit(sta,base);meta=json.loads((sta/'manifest.json').read_text())
    if meta['cst_sha256']!=sha(a.cst) or meta['source_sdc_sha256']!=sha(a.sdc):raise ValueError('Fixture changed the fixed external contract')
    out=a.out.resolve();out.mkdir(exist_ok=False);shutil.copyfile(__file__,out/'runner_source.py.txt')
    design=json.loads((base/'packed.json').read_text());placed=json.loads((base/'placement.json').read_text());changed,records=fixture(design,placed)
    result=dict(status='running',calibration_only=True,source=source,fixture=records,stages=[],budget_seconds=a.seconds,
        storage_reservation=storage,runner_sha256=sha(out/'runner_source.py.txt'),vendor_placement_executed=False,vendor_routing_executed=False,
        timing_signoff=False,full_bitstream_verified=False,source_cst_sha256=sha(a.cst),source_sdc_sha256=sha(a.sdc))
    def save():
        result['wall_seconds']=time.monotonic()-start;(out/'manifest.json').write_text(json.dumps(result,indent=2))
    def run(label,script,args):
        reserve(ROOT,500_000_000);remaining=deadline-time.monotonic()
        if remaining<=0:raise TimeoutError('Complete constant fixture deadline')
        command=[sys.executable,str(ROOT/script),*map(str,args)];row=dict(stage=label,command=command,status='running');result['stages'].append(row);save();stamp=time.monotonic()
        with (out/(label+'.log')).open('w') as log:
            proc=subprocess.Popen(command,cwd=ROOT,stdout=log,stderr=subprocess.STDOUT,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            try:code=proc.wait(timeout=remaining)
            except subprocess.TimeoutExpired:
                stop_owned_process_tree(proc);row.update(status='timeout',seconds=time.monotonic()-stamp);save();raise TimeoutError(label)
        row.update(status='passed' if code==0 else 'failed',returncode=code,seconds=time.monotonic()-stamp);save()
        if code:raise RuntimeError('Constant fixture stage failed: '+label)
    save()
    try:
        placement=out/'placement';placement.mkdir();proof_budget=min(120,int(deadline-time.monotonic()))
        if proof_budget<1:raise TimeoutError('No remaining equivalence-check budget')
        proof=prove(design,changed,placement/'cec',proof_budget)
        if not proof or not all(r['cec']['equivalent'] for r in proof):raise ValueError('Unproved constant fixture')
        (placement/'packed.json').write_text(json.dumps(changed,indent=2));shutil.copyfile(base/'placement.json',placement/'placement.json')
        pm=json.loads((base/'manifest.json').read_text());pm.update(proof=proof,netlist_sha256=sha(placement/'packed.json'),placement_sha256=sha(placement/'placement.json'),constant_fixture=records)
        (placement/'manifest.json').write_text(json.dumps(pm,indent=2));result['proof']=proof;save()
        routes=out/'route';budget=min(240,int(deadline-time.monotonic())-120)
        if budget<1:raise TimeoutError('No routing/final-export budget')
        run('route','scripts/run_ownpr_timing_route.py',['--placement',placement,'--clock-tree',Path(source['routes'])/'clock_tree.json',
            '--reuse-routes',source['routes'],'--timeout',budget,'--out',routes])
        export=out/'export';budget=min(180,int(deadline-time.monotonic()))
        if budget<1:raise TimeoutError('No final-export budget')
        run('final_export','scripts/export_ownpr.py',['--placement',placement,'--routes',routes,'--clock-capture',source['clock_capture'],
            '--cst',a.cst.resolve(),'--sdc',a.sdc.resolve(),'--io-reference',a.io_reference.resolve(),'--seconds',budget,'--out',export])
        observed=audit(export/'native',placement);result['constant_routes']=observed['constant_routes'];result['metrics']=metric(export/'native')
        if len(result['constant_routes']['networks'])!=2 or sum(len(n['sinks']) for n in result['constant_routes']['networks'])!=4:raise ValueError('Incomplete routed constant fixture coverage')
        result.update(status='constant_fixture_complete',placement=str(placement),routes=str(routes),sta=str(export/'native'),configuration_bits_verified=observed['configuration_bits_verified'])
    except Exception as error:
        result.update(status='timeout' if isinstance(error,TimeoutError) else 'failed',error=str(error));save();raise
    save();print(json.dumps({k:result[k] for k in ('status','wall_seconds','metrics','constant_routes')},indent=2))


if __name__=='__main__':main()
