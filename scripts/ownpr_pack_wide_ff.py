"""CEC-proved identity buffer absorption into a calibrated wide-root FF site.

One isolated wide macro can acquire one root-matched DFFRE. Physical clock
endpoints change and must be replanned before full route/STA acceptance.
"""
import argparse,copy,json,sys,time
from pathlib import Path
from ownpr_device import ROOT,Device
from ownpr_image_storage import load_json,sha
from ownpr_mux_macros import tree,validate
from ownpr_ff_configuration import compile_initial
from ownpr_wide_ff_packing import pairs
from native_boundary_cec import prove
from workspace_budget import reserve


def transform(design,placed,buffer):
    cells=design['modules']['top']['cells'];site=placed['clusters'][buffer];lut=cells[buffer]
    if not site['ff'] or site.get('wide_ff_root') or lut['type'] not in ('LUT1','LUT2','LUT3','LUT4'):raise ValueError('Expected ordinary paired identity buffer')
    width=int(lut['type'][-1]);truth=int(lut['parameters']['INIT'],2)
    pins=[p for p in range(width) if truth==sum(((a>>p)&1)<<a for a in range(1<<width))]
    if len(pins)!=1:raise ValueError('Only exact identity buffers can be absorbed')
    src=lut['connections']['I'+str(pins[0])][0];dst=lut['connections']['F'][0];ff=site['ff']
    if cells[ff]['connections']['D']!=[dst]:raise ValueError('FF does not consume paired buffer')
    drivers=[n for n,c in cells.items() for p,bs in c['connections'].items() if c['port_directions'][p]=='output' and src in bs]
    if len(drivers)!=1 or not cells[drivers[0]]['type'].startswith('MUX2_LUT'):raise ValueError('Identity source is not a unique wide root')
    root=drivers[0];macros=[m for m in placed.get('wide_macros',[]) if m['root']==root]
    if len(macros)!=1:raise ValueError('Missing wide macro root')
    macro=macros[0];spec=tree(design,root,macro['root_slot']);leaf=next(n for n,s in spec['leaves'].items() if s==macro['root_slot'])
    new=copy.deepcopy(design);np=copy.deepcopy(placed);nc=new['modules']['top']['cells'];del nc[buffer];del np['clusters'][buffer]
    # Merge two logically identical nets and retain the FF's original D bit.
    for c in nc.values():
        for pin,bits in c['connections'].items():c['connections'][pin]=[dst if b==src else b for b in bits]
    for table in ('ports','netnames'):
        for record in new['modules']['top'].get(table,{}).values():record['bits']=[dst if b==src else b for b in record['bits']]
    np['clusters'][leaf]['ff']=ff;np['clusters'][leaf]['wide_ff_root']=root
    device=Device();validate(new,np,device);compile_initial(new,np);check=pairs(new,np,device)
    return new,np,dict(buffer_removed=buffer,root=root,ff=ff,old_ff_site=[site['row'],site['column'],site['slot']],new_ff_site=[macro['row'],macro['column'],macro['root_slot']],
        lut_growth=-1,interface_cycles_changed=False,clock_replan_required=True,wide_ff_pairs=check)


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--base',type=Path,required=True);p.add_argument('--buffer',action='append',required=True);p.add_argument('--out',type=Path,required=True);a=p.parse_args()
    reserve(ROOT,50_000_000);start=time.monotonic();old=load_json(a.base/'packed.json');placed=load_json(a.base/'placement.json')
    new,np=old,placed;changes=[]
    for buffer in a.buffer:
        new,np,change=transform(new,np,buffer);changes.append(change)
    change=changes[0] if len(changes)==1 else dict(transformations=changes,lut_growth=-len(changes),interface_cycles_changed=False,clock_replan_required=True)
    a.out.mkdir(exist_ok=False);proof=prove(old,new,a.out/'cec',120)
    (a.out/'packed.json').write_text(json.dumps(new,indent=2));(a.out/'placement.json').write_text(json.dumps(np,indent=2))
    m=load_json(a.base/'manifest.json');m.update(status='placed',netlist_sha256=sha(a.out/'packed.json'),placement_sha256=sha(a.out/'placement.json'),proof=proof,wide_ff_packing=change,
        source_netlist_sha256=sha(a.base/'packed.json'),source_placement_sha256=sha(a.base/'placement.json'),clock_endpoints_unchanged=False,timing_signoff=False,wall_seconds=time.monotonic()-start)
    (a.out/'manifest.json').write_text(json.dumps(m,indent=2));print(json.dumps(change,indent=2))


if __name__=='__main__':main()
