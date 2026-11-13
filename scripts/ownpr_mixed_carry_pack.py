"""Pack unpaired ordinary LUTs into calibrated free carry-CFU slots6/7.

Geometry ranks candidates only. Netlist/state/pins stay exact; routing and
native complete STA must decide whether mixed packing improves performance.
"""
import argparse,copy,json,shutil
from collections import defaultdict
from pathlib import Path
from ownpr_device import ROOT,Device
from ownpr_image_storage import load_json,sha
from ownpr_critical_chain_move import locations
from ownpr_state_safety import protected_cells
from ownpr_carry_lut_sharing import calibration
from ownpr_carry_macros import validate as validate_carry
from ownpr_mux_macros import validate as validate_mux
from ownpr_ff_configuration import compile_initial
from workspace_budget import reserve


def pack(design,layout,device):
    evidence=calibration();cells=design['modules']['top']['cells'];points=locations(layout);protected=protected_cells(design)
    carry={(v['row'],v['column']) for v in layout.get('arithmetic_clusters',{}).values()}
    wide={(v['row'],v['column']) for v in layout.get('wide_macros',[])}
    occupied={(v['row'],v['column'],v['slot']) for v in layout['clusters'].values()}
    allowed={n for n,v in layout['clusters'].items() if not v.get('ff') and not v.get('wide_ff_root') and (v['row'],v['column']) not in wide and n not in protected and cells[n]['type'] in ('LUT1','LUT2','LUT3','LUT4')}
    drivers={b:n for n,c in cells.items() for p,bs in c['connections'].items() if c['port_directions'][p]=='output' for b in bs}
    incident=defaultdict(list)
    for n,c in cells.items():
        for p,bs in c['connections'].items():
            if c['port_directions'][p]!='input' or p in ('CLK','CLKA','CLKB','CIN'):continue
            for bit in bs:
                source=drivers.get(bit)
                if source not in points or n not in points or source==n:continue
                weight=2. if c['type']=='ALU' or cells[source]['type']=='ALU' else 1.
                incident[n].append((source,weight));incident[source].append((n,weight))
    result=copy.deepcopy(layout);moved=[]
    for r,col in sorted(carry):
        if device.grid[r][col]!=17:continue
        for slot in (6,7):
            if (r,col,slot) in occupied:continue
            choices=[]
            for n in allowed:
                def cost(point):return sum(w*(abs(point[0]-points[other][0])+abs(point[1]-points[other][1])) for other,w in incident[n])
                gain=cost(points[n])-cost((r,col))
                if gain>0:choices.append((-gain,abs(r-points[n][0])+abs(col-points[n][1]),n))
            if not choices:continue
            negative,_,name=min(choices);prior=copy.deepcopy(result['clusters'][name]);result['clusters'][name].update(row=r,column=col,slot=slot)
            points[name]=(r,col);occupied.add((r,col,slot));allowed.remove(name)
            moved.append(dict(lut=name,previous=[prior['row'],prior['column'],prior['slot']],destination=[r,col,slot],ranking_distance_gain=-negative))
    if moved:result['mixed_carry_lut_calibration']=evidence['capture_sha256']
    compile_initial(design,result);validate_carry(design,result,device)
    if result.get('wide_macros'):validate_mux(design,result,device)
    return result,moved


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--placement',type=Path,required=True);p.add_argument('--out',type=Path,required=True);a=p.parse_args();reserve(ROOT,20_000_000)
    d=load_json(a.placement/'packed.json');before=load_json(a.placement/'placement.json');after,moves=pack(d,before,Device());a.out.mkdir(exist_ok=False)
    shutil.copyfile(a.placement/'packed.json',a.out/'packed.json');(a.out/'placement.json').write_text(json.dumps(after,indent=2));shutil.copyfile(__file__,a.out/'runner_source.py.txt')
    meta=load_json(a.placement/'manifest.json');meta.update(status='placed',placement_sha256=sha(a.out/'placement.json'),netlist_sha256=sha(a.out/'packed.json'),source_placement_sha256=sha(a.placement/'placement.json'),mixed_packing_moves=moves,
        logical_netlist_byte_identical=True,clock_endpoints_unchanged=True,ff_positions_unchanged=True,timing_signoff=False,
        mixed_packing_implementation_sha256=sha(Path(__file__)))
    (a.out/'manifest.json').write_text(json.dumps(meta,indent=2));print(json.dumps(dict(status='mixed_carry_lut_candidate',moved_luts=len(moves),calibration=after.get('mixed_carry_lut_calibration'))))


if __name__=='__main__':main()
