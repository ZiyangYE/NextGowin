"""Place an equivalent wide-cut replacement while freezing all other cells."""
import argparse,copy,json,shutil
from pathlib import Path
from ownpr_device import Device
from ownpr_mux_architecture import MuxArchitecture
from ownpr_mux_macros import tree,validate
from ownpr_place import sha


def main():
    p=argparse.ArgumentParser();p.add_argument('--base',type=Path,required=True);p.add_argument('--cone',type=Path,required=True);p.add_argument('--out',type=Path,required=True);a=p.parse_args()
    meta=json.loads((a.cone/'manifest.json').read_text());original=json.loads((a.base/'placement.json').read_text());design=json.loads((a.cone/'packed.json').read_text())
    if (meta['status']!='logic_equivalent_only' or meta['source_sha256']!=sha(a.base/'packed.json')
        or meta['netlist_sha256']!=sha(a.cone/'packed.json') or not meta['proof']
        or not all(r['cec']['equivalent'] for r in meta['proof'])):raise ValueError('Wide replacement lacks matching equivalence proof')
    architecture=MuxArchitecture();device=Device();root=meta['transform']['replaced_root'];old=original['clusters'][root]
    if old['ff']:raise ValueError('Wide replacement of a paired FF LUT needs a separate packing rule')
    placed=copy.deepcopy(original);del placed['clusters'][root];spec=tree(design,root)
    used={(r['row'],r['column']) for r in placed['clusters'].values()}|{(r['row'],r['column']) for r in placed.get('arithmetic_clusters',{}).values()}|{(r['row'],r['column']) for r in placed.get('mux_clusters',{}).values()}
    options=[((r-old['row'])**2+(c-old['column'])**2,r,c) for r,row in enumerate(device.grid) for c,typ in enumerate(row) if typ==17 and (r,c) not in used]
    if not options:raise ValueError('No free calibrated CFU for wide macro')
    _,r,c=min(options)
    for name,slot in spec['leaves'].items():placed['clusters'][name]=dict(lut=name,ff=None,row=r,column=c,slot=slot)
    placed.setdefault('mux_clusters',{}).update({n:dict(row=r,column=c,**x) for n,x in spec['muxes'].items()})
    placed.setdefault('wide_macros',[]).append(dict(root=root,row=r,column=c,root_slot=spec['muxes'][root]['slot']))
    check=validate(design,placed,device);placed.update(physical_legality_complete=False,timing_signoff=False,wide_macro_placement=check)
    a.out.mkdir(exist_ok=False);shutil.copyfile(a.cone/'packed.json',a.out/'packed.json');(a.out/'placement.json').write_text(json.dumps(placed,indent=2));(a.out/'wide_macro_audit.json').write_text(json.dumps(check,indent=2))
    result=json.loads((a.base/'manifest.json').read_text());result.update(status='placed',netlist_sha256=sha(a.out/'packed.json'),placement_sha256=sha(a.out/'placement.json'),proof=meta['proof'],
        wide_replacement=dict(root=root,old_site=[old['row'],old['column'],old['slot']],new_cfu=[r,c],fixed_other_cells=True,architecture_sha256=architecture.sha256),timing_signoff=False,physical_legality_complete=False)
    (a.out/'manifest.json').write_text(json.dumps(result,indent=2));print(json.dumps(result['wide_replacement']))


if __name__=='__main__':main()
