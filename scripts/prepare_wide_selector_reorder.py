"""Prove a wide selector rewrite and retain byte-identical physical placement."""
import argparse,json,shutil,time
from pathlib import Path
from ownpr_device import ROOT,Device
from ownpr_image_storage import load_json,sha,copy_original
from ownpr_wide_selector_reorder import transform
from ownpr_mux_macros import validate
from native_boundary_cec import prove
from workspace_budget import reserve


def main():
    p=argparse.ArgumentParser();p.add_argument('--base',type=Path,required=True);p.add_argument('--root',required=True);p.add_argument('--late-bit',type=int,required=True);p.add_argument('--out',type=Path,required=True);a=p.parse_args()
    reserve(ROOT,20000000);stamp=time.monotonic();meta=load_json(a.base/'manifest.json')
    if meta['status']!='placed' or sha(a.base/'packed.json')!=meta['netlist_sha256'] or sha(a.base/'placement.json')!=meta['placement_sha256']:raise ValueError('Source placement checkpoint changed')
    old=load_json(a.base/'packed.json');placed=load_json(a.base/'placement.json');changed,record=transform(old,a.root,a.late_bit)
    validate(changed,placed,Device());a.out.mkdir(exist_ok=False);proof=prove(old,changed,a.out/'cec',120)
    if not all(r['cec']['equivalent'] for r in proof):raise ValueError('Boundary CEC did not pass')
    (a.out/'packed.json').write_text(json.dumps(changed,indent=2));copy_original(a.base/'placement.json',a.out/'placement.json')
    if sha(a.out/'placement.json')!=sha(a.base/'placement.json'):raise ValueError('Selector rewrite moved physical cells')
    for name in ('ownpr_wide_selector_reorder.py','prepare_wide_selector_reorder.py'):shutil.copyfile(ROOT/'scripts'/name,a.out/(name+'.txt'))
    result=dict(status='placed',netlist_sha256=sha(a.out/'packed.json'),placement_sha256=sha(a.out/'placement.json'),source_netlist_sha256=sha(a.base/'packed.json'),source_placement_sha256=sha(a.base/'placement.json'),rewrite=record,proof=proof,placement_byte_identical=True,state_cells_changed=False,clock_endpoints_unchanged=True,fixed_io_unchanged=True,timing_signoff=False,wall_seconds=time.monotonic()-stamp,helper_sha256={name:sha(a.out/(name+'.txt')) for name in ('ownpr_wide_selector_reorder.py','prepare_wide_selector_reorder.py')})
    (a.out/'manifest.json').write_text(json.dumps(result,indent=2));print(json.dumps({k:v for k,v in result.items() if k!='proof'},indent=2))


if __name__=='__main__':main()
