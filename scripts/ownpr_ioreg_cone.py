"""Physical fanin-cone relocation for fixed-pad output registers.

Coordinates rank candidates only, never bound setup/hold delay. Macro members
and paired state stay fixed; every affected net is rerouted by the caller.
"""
import copy
from collections import defaultdict
from ownpr_device import Device,ROOT
from ownpr_image_storage import load_json
from ownpr_mux_macros import validate as validate_mux
from ownpr_carry_macros import validate as validate_carry
from ownpr_ff_configuration import compile_initial
from ownpr_ioreg_macros import validate as validate_io


def pull_cones(design, placed, depth=3, fraction=.6):
    if not 1<=depth<=6 or not 0<fraction<=1:raise ValueError('Invalid cone depth/fraction')
    cells=design['modules']['top']['cells'];drivers={};loads=defaultdict(set)
    for n,c in cells.items():
        for pin,bits in c['connections'].items():
            for bit in bits:
                if c['port_directions'][pin]=='output':drivers[bit]=n
                else:loads[bit].add(n)
    result=copy.deepcopy(placed);device=Device()
    cal=load_json(ROOT/'results/native_device_lut_004/lut_encoding_audit.json')
    if cal['source_fse_sha256']!=device.fse.sha256 or not all(v['match'] for v in cal['checks']):raise ValueError('LUT calibration changed')
    excluded={(v['row'],v['column']) for kind in ('arithmetic_clusters','mux_clusters') for v in placed.get(kind,{}).values()}
    for v in placed.get('hard_clusters',{}).values():excluded.update((v['row'],v['column']+i) for i in range(3))
    anchors=defaultdict(list)
    for ff,group in sorted(placed.get('ioreg_clusters',{}).items()):
        frontier=[(drivers.get(cells[ff]['connections']['D'][0]),1)];seen=set()
        while frontier:
            n,level=frontier.pop(0)
            if n in seen or n not in placed['clusters'] or level>depth:continue
            seen.add(n);loc=placed['clusters'][n]
            if not cells[n]['type'].startswith('LUT') or loc.get('ff') or (loc['row'],loc['column']) in excluded:continue
            # Native IO coordinates are one-based; core cluster coordinates zero-based.
            anchors[n].append((group['site'][0]-1,group['site'][1]-1,level,ff))
            frontier.extend((drivers.get(b),level+1) for pin,bits in cells[n]['connections'].items() if cells[n]['port_directions'][pin]=='input' for b in bits)
    movable=set(anchors);selected_ff=set(placed.get('ioreg_clusters',{}))
    occupied={(v['row'],v['column'],v['slot']) for v in result['clusters'].values()}
    legal_tiles=[(y,x) for y in range(device.rows) for x in range(device.cols) if (y,x) not in excluded and device.grid[y][x] in cal['tile_types']]
    moves=[]
    for n in sorted(anchors,key=lambda n:(min(a[2] for a in anchors[n]),n)):
        loc=result['clusters'][n];old=tuple(loc[k] for k in ('row','column','slot'))
        outputs=[b for pin,bits in cells[n]['connections'].items() if cells[n]['port_directions'][pin]=='output' for b in bits]
        external=set().union(*(loads[b] for b in outputs))-movable-selected_ff
        weights=[1/a[2] for a in anchors[n]];weight=sum(weights)
        target=[sum(a[i]*w for a,w in zip(anchors[n],weights))/weight for i in (0,1)]
        strength=fraction/min(a[2] for a in anchors[n])/(1+len(external))
        target=[old[i]+strength*(target[i]-old[i]) for i in (0,1)]
        occupied.remove(old)
        candidates=((abs(y-target[0])+abs(x-target[1]),abs(y-old[0])+abs(x-old[1]),s!=old[2],y,x,s) for y,x in legal_tiles for s in range(8) if (y,x,s) not in occupied)
        *_,y,x,s=min(candidates);new=(y,x,s);occupied.add(new)
        loc.update(row=y,column=x,slot=s)
        if new!=old:moves.append(dict(instance=n,old_site=old,new_site=new,external_loads=sorted(external),depth=min(a[2] for a in anchors[n]),target=target))
    if not moves:raise ValueError('No eligible cone movement')
    validate_mux(design,result,device);validate_carry(design,result,device);compile_initial(design,result);validate_io(design,result,device)
    if result.get('hard_clusters'):
        from ownpr_hard_architecture import HardArchitecture
        HardArchitecture().validate(design,result,device)
    assert result['fixed_io']==placed['fixed_io']
    result.update(physical_legality_complete=False,timing_signoff=False)
    return result,dict(moves=moves,depth=depth,fraction=fraction,ranking_only=True,delay_bound=False,netlist_unchanged=True,paired_state_and_macros_fixed=True)
