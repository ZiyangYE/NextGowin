"""Move selected FFs to their existing fixed output pads; preserve the netlist."""
import argparse,json,shutil
from pathlib import Path
from ownpr_ioreg_macros import relocate
from ownpr_place import sha


def main():
    p=argparse.ArgumentParser();p.add_argument('--base',type=Path,required=True);p.add_argument('--port',action='append',required=True);p.add_argument('--out',type=Path,required=True)
    p.add_argument('--cone-depth',type=int,default=0);p.add_argument('--cone-fraction',type=float,default=.6);a=p.parse_args()
    meta=json.loads((a.base/'manifest.json').read_text())
    if meta['status']!='placed' or sha(a.base/'placement.json')!=meta['placement_sha256'] or sha(a.base/'packed.json')!=meta['netlist_sha256']:raise ValueError('Source placement changed')
    design=json.loads((a.base/'packed.json').read_text());placed=json.loads((a.base/'placement.json').read_text());changed,changes,check=relocate(design,placed,a.port)
    if a.cone_depth:
        from ownpr_ioreg_cone import pull_cones
        changed,cone=pull_cones(design,changed,a.cone_depth,a.cone_fraction)
        meta['ioreg_cone_relocation']=cone
    a.out.mkdir(exist_ok=False);shutil.copyfile(a.base/'packed.json',a.out/'packed.json');(a.out/'placement.json').write_text(json.dumps(changed,indent=2));(a.out/'ioreg_capacity_audit.json').write_text(json.dumps(check,indent=2))
    meta.update(status='placed',netlist_sha256=sha(a.out/'packed.json'),placement_sha256=sha(a.out/'placement.json'),ioreg_relocation=changes,
        source_placement_sha256=sha(a.base/'placement.json'),logical_netlist_byte_identical=True,state_cells_changed=False,pipeline_stages_added=0,
        timing_signoff=False,physical_legality_complete=False)
    (a.out/'manifest.json').write_text(json.dumps(meta,indent=2));print(json.dumps(changes))


if __name__=='__main__':main()
