"""Match the calibrated single paired-FF envelope without moving any state."""
import argparse,copy,json,shutil
from pathlib import Path
from ownpr_device import ROOT,Device
from ownpr_image_storage import load_json,sha
from ownpr_ff_configuration import compile_initial
from ownpr_mux_macros import validate as validate_mux
from workspace_budget import reserve


def main():
    p=argparse.ArgumentParser();p.add_argument('--placement',type=Path,required=True);p.add_argument('--out',type=Path,required=True);a=p.parse_args();reserve(ROOT,15000000)
    design=load_json(a.placement/'packed.json');old=load_json(a.placement/'placement.json');new=copy.deepcopy(old);device=Device()
    isolated={(v['row'],v['column']) for v in old['clusters'].values() if v.get('ff') and v['slot'] not in (0,2,4) and not v.get('wide_ff_root')}
    reserved=isolated|{(v['row'],v['column']) for v in old.get('arithmetic_clusters',{}).values()}|{(v['row'],v['column']) for v in old.get('mux_clusters',{}).values()}
    reserved.update((v['row'],v['column']+i) for v in old.get('hard_clusters',{}).values() for i in range(3))
    occupied={(v['row'],v['column'],v['slot']) for v in old['clusters'].values()};moves=[]
    for name,row in old['clusters'].items():
        if (row['row'],row['column']) not in isolated or row.get('ff'):continue
        origin=(row['row'],row['column']);choices=[]
        for r in range(max(0,origin[0]-8),min(device.rows,origin[0]+9)):
            for col in range(max(0,origin[1]-8),min(device.cols,origin[1]+9)):
                if (r,col) in reserved or device.grid[r][col]!=17:continue
                for slot in range(8):
                    if (r,col,slot) not in occupied:choices.append((abs(r-origin[0])+abs(col-origin[1]),r,col,slot))
        if not choices:raise ValueError('No legal sibling-LUT evacuation site')
        _,r,col,slot=min(choices);new['clusters'][name].update(row=r,column=col,slot=slot);occupied.add((r,col,slot));moves.append(dict(lut=name,previous=[*origin,row['slot']],destination=[r,col,slot]))
    compile_initial(design,new)
    if new.get('wide_macros'):validate_mux(design,new,device)
    a.out.mkdir(exist_ok=False);shutil.copyfile(a.placement/'packed.json',a.out/'packed.json');(a.out/'placement.json').write_text(json.dumps(new,indent=2))
    meta=load_json(a.placement/'manifest.json');meta.update(status='placed',placement_sha256=sha(a.out/'placement.json'),source_placement_sha256=sha(a.placement/'placement.json'),extended_ff_isolation_moves=moves,ff_positions_unchanged=True,timing_signoff=False)
    (a.out/'manifest.json').write_text(json.dumps(meta,indent=2));print(json.dumps(dict(status='extended_ff_cfus_isolated',moved_luts=len(moves))))


if __name__=='__main__':main()
