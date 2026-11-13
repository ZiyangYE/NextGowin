"""Place a proven output-state cache with unchanged pins and original state.

Only newly added LUTs move. Weighted distance ranks placement, not timing.
The new IO register uses the already-calibrated CE=1/RESET=0 profile.
"""
import argparse,copy,hashlib,json,shutil,statistics,time
from pathlib import Path
from ownpr_device import ROOT,Device
from ownpr_image_storage import load_json,sha,copy_original
from ownpr_output_state_cache import verify
from ownpr_place import constraints
from ownpr_critical_chain_move import locations,exclusive_tiles
from ownpr_ioreg_macros import site_for,validate as validate_io
from ownpr_mux_macros import validate as validate_mux
from ownpr_carry_macros import validate as validate_carry
from ownpr_ff_configuration import compile_initial
from workspace_budget import reserve


def digest(value):return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def prepare(base,proof_folder,simulation,cst,sdc,out):
    reserve(ROOT,20000000);stamp=time.monotonic();old=load_json(base/'packed.json');original=load_json(base/'placement.json');bm=load_json(base/'manifest.json')
    if bm['status']!='placed' or bm['netlist_sha256']!=sha(base/'packed.json') or bm['placement_sha256']!=sha(base/'placement.json'):raise ValueError('Base placement changed')
    design=load_json(proof_folder/'packed.json');record=load_json(proof_folder/'manifest.json');sim=load_json(simulation/'manifest.json');obuf,ff=record['obuf'],record['new_ff']
    if sha(base/'packed.json')!=record['source_netlist_sha256'] or sha(proof_folder/'packed.json')!=record['new_netlist_sha256']:raise ValueError('Functional certificate source changed')
    if sim['status']!='vendor_model_exhaustive_step_passed' or sim['source_design_canonical_sha256']!=digest(old) or sim['candidate_design_canonical_sha256']!=digest(design) or sha(simulation/'tb.v')!=sim['testbench_sha256']:raise ValueError('Vendor simulation does not match this candidate')
    from gowin_installation import SDK
    if sha(SDK/'simlib/gw5a/prim_sim.v')!=sim['primitive_model_sha256']:raise ValueError('Vendor primitive model changed')
    if sim.get('general_function',False)!=record.get('general_function',False):raise ValueError('Output function proof mode differs from simulation')
    checked=verify(old,design,obuf,ff,record.get('general_function',False))
    contract=constraints(old['modules']['top'],cst.read_text(),sdc.read_text());cells=design['modules']['top']['cells'];clock=cells[ff]['connections']['CLK'];clock_port=old['modules']['top']['ports'][contract['clock_port']]
    if clock_port['direction']!='input' or len(clock_port['bits'])!=1 or len([c for c in cells.values() if c['type']=='IBUF' and c['connections']['O']==clock and c['connections']['I']==clock_port['bits']])!=1:raise ValueError('Output cache clock is not the constrained primary clock')
    pad=original['fixed_io'][obuf];bit=cells[obuf]['connections']['O'][0]
    if contract['ports'].get(bit)!=pad['port'] or contract['pins'][pad['port']]!=pad['package_pin']:raise ValueError('Frozen output port differs from the checked IO contract')
    placed=copy.deepcopy(original);site=site_for(pad)
    if any(g['iobuf']==obuf for g in placed.get('ioreg_clusters',{}).values()):raise ValueError('Output already has an IO register')
    placed.setdefault('ioreg_clusters',{})[ff]=dict(iobuf=obuf,site=site)
    device=Device();cal=load_json(ROOT/'results/native_device_lut_004/lut_encoding_audit.json')
    if cal['source_fse_sha256']!=device.fse.sha256 or not all(r['match'] for r in cal['checks']):raise ValueError('LUT placement calibration changed')
    added=record['added_luts'];points=locations(placed);points[ff]=(site[0]-1,site[1]-1);anchor=points[ff]
    drivers={b:n for n,c in cells.items() for p,bs in c['connections'].items() if c['port_directions'][p]=='output' for b in bs}
    edges=[]
    for n,c in cells.items():
        for p,bs in c['connections'].items():
            if c['port_directions'][p]!='input' or p in ('CLK','CE','RESET'):continue
            for b in bs:
                source=drivers.get(b)
                if source and (source in added or n in added) and (source in points or source in added) and (n in points or n in added):edges.append((source,n,3 if n==ff else 1))
    occupied={(p['row'],p['column'],p['slot']) for p in placed['clusters'].values()};excluded=exclusive_tiles(placed)
    tiles=[(r,c) for r in range(device.rows) for c in range(device.cols) if device.grid[r][c] in cal['tile_types'] and (r,c) not in excluded]
    def choose(target,score):
        nearby=[p for p in tiles if abs(p[0]-target[0])<=5 and abs(p[1]-target[1])<=5]
        choices=((score(r,c),abs(r-target[0])+abs(c-target[1]),r,c,s) for r,c in (nearby or tiles) for s in range(8) if (r,c,s) not in occupied)
        try:*_,r,c,s=min(choices)
        except ValueError:raise ValueError('No legal new-LUT slot near output cone')
        return r,c,s
    for name in added:
        parents=[points[a] for a,b,w in edges if b==name and a in points]
        targets=parents+[anchor];target=tuple(statistics.median(p[i] for p in targets) for i in (0,1))
        r,c,s=choose(target,lambda r,c:sum(abs(r-y)+abs(c-x) for y,x in targets));occupied.add((r,c,s));points[name]=(r,c);placed['clusters'][name]=dict(lut=name,ff=None,row=r,column=c,slot=s)
    for iteration in range(4):
        for name in (added if iteration%2 else list(reversed(added))):
            row=placed['clusters'][name];occupied.remove((row['row'],row['column'],row['slot']));incident=[e for e in edges if name in e[:2]]
            targets=[points[b if a==name else a] for a,b,w in incident for _ in range(w)];target=tuple(statistics.median(p[i] for p in targets) for i in (0,1))
            r,c,s=choose(target,lambda r,c:sum(abs(r-y)+abs(c-x) for y,x in targets));occupied.add((r,c,s));points[name]=(r,c);row.update(row=r,column=c,slot=s)
    if placed['fixed_io']!=original['fixed_io'] or any(placed['clusters'][n]!=row for n,row in original['clusters'].items()):raise ValueError('Original pin or state/logic placement changed')
    validate_io(design,placed,device);validate_mux(design,placed,device);validate_carry(design,placed,device);compile_initial(design,placed)
    if placed.get('hard_clusters'):
        from ownpr_hard_architecture import HardArchitecture
        HardArchitecture().validate(design,placed,device)
    placed.update(physical_legality_complete=False,timing_signoff=False);out.mkdir(exist_ok=False);copy_original(proof_folder/'packed.json',out/'packed.json');(out/'placement.json').write_text(json.dumps(placed,indent=2))
    result=dict(status='placed',netlist_sha256=sha(out/'packed.json'),placement_sha256=sha(out/'placement.json'),source_netlist_sha256=sha(base/'packed.json'),source_placement_sha256=sha(base/'placement.json'),cst_sha256=sha(cst),sdc_sha256=sha(sdc),proof=checked,simulation_manifest_sha256=sha(simulation/'manifest.json'),new_ff=ff,new_luts=added,new_io_site=site,fixed_pin=pad['package_pin'],output_port=pad['port'],same_declared_clock_verified=True,original_cell_locations_unchanged=True,clock_replan_required=True,interface_cycles_changed=False,placement_ranking='New-LUT incident distance only; full IO STA pending',timing_signoff=False,wall_seconds=time.monotonic()-stamp)
    (out/'manifest.json').write_text(json.dumps(result,indent=2));return result


if __name__=='__main__':
    p=argparse.ArgumentParser()
    for n in ('base','proof-folder','simulation','cst','sdc','out'):p.add_argument('--'+n,type=Path,required=True)
    a=p.parse_args();print(json.dumps(prepare(a.base,a.proof_folder,a.simulation,a.cst,a.sdc,a.out),indent=2))
