"""CEC-proved I3 mapping of critical identity/inverting LUTs paired with FFs.

This retains an explicit LUT and its FF. It does not claim a standalone-FF
bypass or wide-MUX/FF sharing mode.
"""
import argparse,copy,json,shutil,time
from pathlib import Path
from ownpr_device import ROOT,Device
from ownpr_image_storage import load_json,sha
from ownpr_lut_lifting import lift
from ownpr_lut_pin_eco import permute


def transform(design,placed,priorities,limit=8):
    cells=design['modules']['top']['cells'];ground=next(c['connections']['G'][0] for c in cells.values() if c['type']=='GND');constants={b for c in cells.values() if c['type'] in ('GND','VCC') for bs in c['connections'].values() for b in bs}
    options=[]
    for name,site in placed['clusters'].items():
        if not site.get('ff') or site.get('independent_ff_input'):continue
        c=cells[name]
        if c['type'] not in ('LUT1','LUT2','LUT3','LUT4'):continue
        width=int(c['type'][-1]);truth=int(c['parameters']['INIT'],2)
        for pin in range(min(width,3)):
            identity=sum(((a>>pin)&1)<<a for a in range(1<<width));inverted=identity^((1<<(1<<width))-1)
            priority=priorities.get((name,'I'+str(pin)),0.)
            if truth not in (identity,inverted) or not priority or c['connections']['I'+str(pin)][0] in constants:continue
            if cells[site['ff']]['connections']['D']!=c['connections']['F']:raise ValueError('FF passthrough is not a direct paired edge')
            options.append((-priority,name,pin,truth==inverted))
    if not options:raise ValueError('No critical paired identity LUT needs a faster input')
    new=copy.deepcopy(design);changes=[]
    for _,name,pin,inverted in sorted(options)[:limit]:
        new['modules']['top']['cells'][name]=permute(lift(cells[name],ground),pin,3)
        changes.append(dict(lut=name,ff=placed['clusters'][name]['ff'],old_pin='I'+str(pin),new_pin='I3',inverting=inverted,priority=priorities[name,'I'+str(pin)]))
    return new,changes


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for n in ('base','sta','out'):p.add_argument('--'+n,type=Path,required=True)
    p.add_argument('--limit',type=int,default=8);p.add_argument('--window',type=float,default=1.);a=p.parse_args()
    if not 1<=a.limit<=256:p.error('Invalid batch size')
    from workspace_budget import reserve
    from ownpr_placement_feedback import load
    from ownpr_ff_configuration import compile_initial
    from ownpr_mux_macros import validate
    from native_boundary_cec import prove
    reserve(ROOT,50000000);start=time.monotonic();old=load_json(a.base/'packed.json');placed=load_json(a.base/'placement.json');meta=load_json(a.sta/'manifest.json')
    if meta['packed_sha256']!=sha(a.base/'packed.json') or meta['placement_sha256']!=sha(a.base/'placement.json'):raise ValueError('Fast FF input feedback mismatch')
    priorities,feedback=load(a.sta,old,relative_window_ns=a.window);new,changes=transform(old,placed,priorities,a.limit);validate(new,placed,Device());compile_initial(new,placed)
    a.out.mkdir(exist_ok=False);proof=prove(old,new,a.out/'cec',120);(a.out/'packed.json').write_text(json.dumps(new,indent=2));shutil.copyfile(a.base/'placement.json',a.out/'placement.json')
    m=load_json(a.base/'manifest.json');m.update(status='placed',netlist_sha256=sha(a.out/'packed.json'),placement_sha256=sha(a.out/'placement.json'),source_netlist_sha256=sha(a.base/'packed.json'),source_sta_sha256=sha(a.sta/'probe.tr'),proof=proof,
        fast_ff_inputs=changes,placement_feedback=feedback,lut_growth=0,ff_locations_unchanged=True,clock_endpoints_unchanged=True,wall_seconds=time.monotonic()-start,timing_signoff=False)
    (a.out/'manifest.json').write_text(json.dumps(m,indent=2));print(json.dumps(changes,indent=2))


if __name__=='__main__':main()
